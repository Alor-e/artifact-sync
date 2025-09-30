import os, json, asyncio, argparse, time, sys, traceback, builtins
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
import tiktoken
from dotenv import load_dotenv
from core.schemas import AnalysisResult, UnsureEntry, DetailedImpactReport
from core.config import AgentConfig
from context.tree_getter import build_tree
from context.diff_getter import get_diffs, create_diff_context
from context.readme_getter import get_readme_content
from llm.manager import AsyncUnifiedChatManager, UnifiedChatManager
from llm.providers.base import ProviderFactory
from analysis.subfolder import analyze_subfolder_async
from analysis.refinement import process_refinement_batch
from analysis.reporting import report_detailed_async
from analysis.fixes import CodeFixGenerator
from utils.helpers import safe_join_path, is_folder_at_max_depth, normalize_path, resolve_repo_path
from utils.git_ignore_handler import create_git_ignore_processor


class _PrintRedirector:
    """Context manager to temporarily redirect `print` output."""

    def __init__(self, stream):
        self.stream = stream
        self._original_print = builtins.print

    def __enter__(self):
        def redirected_print(*args, **kwargs):
            if 'file' not in kwargs or kwargs['file'] is None:
                kwargs['file'] = self.stream
            self._original_print(*args, **kwargs)

        builtins.print = redirected_print

    def __exit__(self, exc_type, exc_val, exc_tb):
        builtins.print = self._original_print


def _initialize_token_bucket() -> Dict[str, int]:
    return {'prompt': 0, 'completion': 0, 'input': 0, 'output': 0, 'total': 0}


def _accumulate_tokens(usage: Optional[dict], bucket: Dict[str, int]):
    if not usage:
        return

    token_map = {
        'prompt_tokens': 'prompt',
        'completion_tokens': 'completion',
        'input_tokens': 'input',
        'output_tokens': 'output',
        'total_tokens': 'total',
        'input_token_count': 'input',
        'output_token_count': 'output',
        'total_token_count': 'total'
    }

    for key, dest in token_map.items():
        value = usage.get(key)
        if value is not None:
            bucket[dest] += int(value)


def _prepare_context(config: AgentConfig):
    git_ignore_processor = create_git_ignore_processor(config.root_path)
    tree_struct = build_tree(
        config.root_path,
        config.max_depth,
        exclude_dirs=None,
        git_ignore_processor=git_ignore_processor
    )
    diffs = get_diffs(config.root_path)
    tree_json = json.dumps(tree_struct, indent=2)
    readme_content = get_readme_content(config.root_path)
    diffs_context = create_diff_context(diffs)

    return {
        'git_ignore_processor': git_ignore_processor,
        'tree_json': tree_json,
        'diffs': diffs,
        'readme_content': readme_content,
        'diffs_context': diffs_context
    }


class EvaluationTracker:
    """Collects metrics, tokens, and artefacts for evaluation runs."""

    def __init__(self, run_index: int, output_dir: Path):
        self.run_index = run_index
        self.output_dir = output_dir
        self.metrics = {
            'run_index': run_index + 1,
            'timing': {},
            'iteration_timings': [],
            'tokens': {},
            'token_details': {},
            'recommendations': [],
            'fixes': [],
            'final_sure': [],
            'still_unsure': [],
            'refinement_stats': []
        }
        self._phase_starts = {}
        self._run_start = time.perf_counter()

    def start_phase(self, phase: str):
        self._phase_starts[phase] = time.perf_counter()

    def end_phase(self, phase: str):
        start = self._phase_starts.pop(phase, None)
        if start is not None:
            self.metrics['timing'][f'{phase}_elapsed_s'] = round(time.perf_counter() - start, 6)

    def record_iteration_elapsed(self, iteration: int, elapsed: float):
        self.metrics['iteration_timings'].append({
            'iteration': iteration,
            'elapsed_s': round(elapsed, 6)
        })

    def _ensure_token_stage(self, stage: str):
        self.metrics['tokens'].setdefault(stage, {
            'prompt': 0,
            'completion': 0,
            'input': 0,
            'output': 0,
            'total': 0
        })
        self.metrics['token_details'].setdefault(stage, [])

    def add_tokens(self, stage: str, usage: Optional[dict]):
        if not usage:
            return
        self._ensure_token_stage(stage)
        totals = self.metrics['tokens'][stage]
        details = self.metrics['token_details'][stage]

        prompt = usage.get('prompt_tokens') or usage.get('input_tokens') or usage.get('input_token_count')
        completion = usage.get('completion_tokens') or usage.get('output_tokens') or usage.get('output_token_count')
        total = usage.get('total_tokens') or usage.get('total_token_count')

        if prompt:
            totals['prompt'] += prompt
        if completion:
            totals['completion'] += completion
        if usage.get('input_tokens'):
            totals['input'] += usage['input_tokens']
        if usage.get('output_tokens'):
            totals['output'] += usage['output_tokens']
        if total:
            totals['total'] += total

        details.append(usage)

    def add_recommendation(self, path: str, parsed, raw_text: str):
        record = {'path': path, 'raw': raw_text}
        if parsed is not None:
            if hasattr(parsed, 'model_dump'):
                record['parsed'] = parsed.model_dump()
            else:
                record['parsed'] = parsed
        self.metrics['recommendations'].append(record)

    def add_fix(self, path: str, content: str, *, mode: str = "full_file", applied_content: Optional[str] = None):
        record = {'path': path, 'content': content, 'mode': mode}
        if applied_content is not None:
            record['applied_content'] = applied_content
        self.metrics['fixes'].append(record)

    def set_results(self, final_sure, still_unsure, refinement_stats):
        self.metrics['final_sure'] = list(final_sure)
        self.metrics['still_unsure'] = still_unsure
        self.metrics['refinement_stats'] = refinement_stats

    def finalize(self):
        total_elapsed = time.perf_counter() - self._run_start
        self.metrics['timing']['total_elapsed_s'] = round(total_elapsed, 6)

        aggregate = {'prompt': 0, 'completion': 0, 'input': 0, 'output': 0, 'total': 0}
        for stage_totals in self.metrics['tokens'].values():
            for key in aggregate:
                aggregate[key] += stage_totals.get(key, 0) or 0

        if aggregate['total'] == 0:
            computed_total = aggregate['prompt'] + aggregate['completion']
            if computed_total == 0:
                computed_total = aggregate['input'] + aggregate['output']
            aggregate['total'] = computed_total

        self.metrics['tokens_aggregate'] = aggregate
        self.metrics['tokens_input_total'] = aggregate['prompt'] + aggregate['input']

    def write_output(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"evaluation_run_{self.run_index + 1}.json"
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(self.metrics, handle, indent=2)


async def run_analysis(args, evaluation_tracker: Optional[EvaluationTracker] = None):
    """Run the full Artifact Sync analysis workflow and return structured results."""

    run_started_at = datetime.utcnow()
    analysis_timer_start = time.perf_counter()
    token_totals = _initialize_token_bucket()

    if evaluation_tracker:
        evaluation_tracker.start_phase('config_load')

    load_dotenv()
    config = AgentConfig.from_env(args)

    if evaluation_tracker:
        evaluation_tracker.end_phase('config_load')

    print(f"Using provider: {config.provider.value}")
    print(f"Using model: {config.model_name}")
    print(f"Repository path: {config.root_path}")
    print(f"Max depth: {config.max_depth}")

    # Build context once
    if evaluation_tracker:
        evaluation_tracker.start_phase('context_build')

    context_bundle = _prepare_context(config)
    git_ignore_processor = context_bundle['git_ignore_processor']
    tree_json = context_bundle['tree_json']
    diffs = context_bundle['diffs']
    readme_content = context_bundle['readme_content']
    diffs_context = context_bundle['diffs_context']

    if evaluation_tracker:
        evaluation_tracker.end_phase('context_build')

    # Create provider + async chat manager
    if evaluation_tracker:
        evaluation_tracker.start_phase('provider_setup')

    provider = ProviderFactory.create_provider(config.provider, config.model_name)
    chat_manager = AsyncUnifiedChatManager(
        provider=provider,
        api_key=config.api_key,
        tree_json=tree_json,
        diffs_context=diffs_context,
        readme_content=readme_content,
        max_retries=config.max_retries,
        max_workers=5,
    )

    if evaluation_tracker:
        evaluation_tracker.end_phase('provider_setup')

    print("\n=== Initial Analysis ===")
    main_chat = chat_manager.get_async_chat("main")
    if evaluation_tracker:
        evaluation_tracker.start_phase('initial_analysis')

    prompt = """
        Given the commit repository tree structure and the latest commit delta in your context:

        Identify files/folders directly or indirectly impacted (sure) and uncertain cases with reason, is_dir, and needed_info.
    """

    response = await main_chat.send_message_async(prompt)
    initial: AnalysisResult = response.parsed
    if evaluation_tracker:
        evaluation_tracker.add_tokens('initial', response.usage)
        evaluation_tracker.end_phase('initial_analysis')
    _accumulate_tokens(response.usage, token_totals)

    print(f"Initial analysis - Sure: {len(initial.sure)}, Unsure: {len(initial.unsure)}")

    # Separate initial sure items into files and folders
    final_sure = []
    remaining_unsure: List[UnsureEntry] = []
    initial_sure_folders: List[str] = []

    for item in initial.sure:
        resolved_item = resolve_repo_path(config.root_path, item)
        if resolved_item != item:
            print(f"[PATH] Resolved initial sure {item} -> {resolved_item}")
        full_path = safe_join_path(config.root_path, resolved_item)
        if os.path.isdir(full_path) and is_folder_at_max_depth(resolved_item, config.root_path, config.max_depth):
            initial_sure_folders.append(resolved_item)
            print(f"[INITIAL] Found sure folder at max depth: {resolved_item}")
        else:
            final_sure.append(resolved_item)

    remaining_unsure.extend(initial.unsure)
    refinement_stats: List[Any] = []

    # Process initial sure folders in parallel
    if initial_sure_folders:
        print(f"\n=== Processing {len(initial_sure_folders)} sure folders in parallel ===")
        folder_tasks = [
            analyze_subfolder_async(
                folder_path,
                config.root_path,
                chat_manager,
                git_ignore_processor,
                config.max_depth,
                evaluation_tracker,
            )
            for folder_path in initial_sure_folders
        ]
        folder_results = await asyncio.gather(*folder_tasks, return_exceptions=True)

        for folder_path, result in zip(initial_sure_folders, folder_results):
            if isinstance(result, Exception):
                print(f"[ERROR] Failed to analyze folder {folder_path}: {result}")
                continue
            sure_files, unsure_entries = result
            final_sure.extend(sure_files)
            remaining_unsure.extend(unsure_entries)

    # Refinement loop
    max_iterations = 3
    iteration = 0

    while remaining_unsure and iteration < max_iterations:
        iteration += 1
        print(f"\n=== Parallel Refinement Iteration {iteration} ({len(remaining_unsure)} entries) ===")
        iter_start = time.perf_counter()
        current_batch = remaining_unsure.copy()
        remaining_unsure = []

        batch_sure, batch_unsure, folders_to_analyze, batch_stats = await process_refinement_batch(
            current_batch,
            config.root_path,
            chat_manager,
            config.max_depth,
            git_ignore_processor,
            evaluation_tracker,
        )

        final_sure.extend(batch_sure)
        remaining_unsure.extend(batch_unsure)
        refinement_stats.extend(batch_stats)

        if folders_to_analyze:
            print(f"[PARALLEL] Analyzing {len(folders_to_analyze)} discovered folders...")
            folder_tasks = [
                analyze_subfolder_async(
                    folder_path,
                    config.root_path,
                    chat_manager,
                    git_ignore_processor,
                    config.max_depth,
                    evaluation_tracker,
                )
                for folder_path in folders_to_analyze
            ]
            folder_results = await asyncio.gather(*folder_tasks, return_exceptions=True)

            for folder_path, result in zip(folders_to_analyze, folder_results):
                if isinstance(result, Exception):
                    print(f"[ERROR] Failed to analyze discovered folder {folder_path}: {result}")
                    continue

                sure_files, unsure_entries = result
                final_sure.extend(sure_files)
                remaining_unsure.extend(unsure_entries)

        escalated_entries: List[UnsureEntry] = []
        pruned_count = 0

        for entry in remaining_unsure.copy():
            if iteration < max_iterations:
                if entry.needed_info in ('file_overview', 'file_metadata'):
                    escalated_entries.append(
                        UnsureEntry(
                            path=entry.path,
                            reason=f"Escalated from overview: {entry.reason}",
                            is_dir=entry.is_dir,
                            needed_info='raw_content',
                        )
                    )
                    print(f"[ESCALATE] {entry.path} -> {entry.needed_info} to raw_content (final chance)")
                elif entry.needed_info == 'raw_content':
                    print(f"[PRUNE] {entry.path} -> low confidence on raw_content, pruning as not impacted")
                    pruned_count += 1
                elif entry.is_dir:
                    if not is_folder_at_max_depth(entry.path, config.root_path, config.max_depth):
                        escalated_entries.append(entry)
                        print(f"[FOLDER] {entry.path} -> scheduling for subfolder analysis")
                    else:
                        print(f"[PRUNE] {entry.path} -> folder at max depth, pruning")
                        pruned_count += 1
                else:
                    print(f"[PRUNE] {entry.path} -> unknown needed_info: {entry.needed_info}")
                    pruned_count += 1
            else:
                print(f"[PRUNE] {entry.path} -> max iterations reached")
                pruned_count += 1

        if pruned_count > 0:
            print(f"[PRUNED] {pruned_count} low-confidence entries pruned (assumed not impacted)")

        remaining_unsure = escalated_entries
        if evaluation_tracker:
            evaluation_tracker.record_iteration_elapsed(iteration, time.perf_counter() - iter_start)

    # Deduplicate / normalize final sure list
    normalized_final: List[str] = []
    seen_paths = set()

    for path in final_sure:
        resolved = resolve_repo_path(config.root_path, path)
        normalized = normalize_path(resolved)
        if normalized not in seen_paths:
            seen_paths.add(normalized)
            normalized_final.append(normalized)
        else:
            print(f"[DEDUP] Removed duplicate: {path} (already have {normalized})")

    final_sure = normalized_final

    # Detailed reporting
    report_entries = await report_detailed_async(final_sure, chat_manager, config.root_path, evaluation_tracker)

    if evaluation_tracker and report_entries:
        needs_update_entries = []
        for entry in report_entries:
            parsed = entry.get('parsed')
            diagnosis = parsed.get('diagnosis') if isinstance(parsed, dict) else None
            if isinstance(diagnosis, dict) and diagnosis.get('needs_update'):
                needs_update_entries.append(entry)

        if needs_update_entries:
            sync_chat_manager = UnifiedChatManager(
                provider=provider,
                api_key=config.api_key,
                tree_json=tree_json,
                diffs_context=diffs_context,
                readme_content=readme_content,
                max_retries=config.max_retries,
            )
            fix_generator = CodeFixGenerator(sync_chat_manager, config.root_path, output_style="diff")

            for entry in needs_update_entries:
                try:
                    impact_report = DetailedImpactReport(**entry['parsed'])
                    result = fix_generator.generate_fix(
                        entry['path'],
                        impact_report,
                        commit_diff=diffs,
                        include_usage=True,
                    )

                    diff_output: Optional[str] = None
                    usage: Optional[Dict[str, Any]] = None

                    if isinstance(result, tuple):
                        if len(result) == 3:
                            fix_content, usage, diff_output = result
                        elif len(result) == 2:
                            fix_content, usage = result
                        else:
                            fix_content = result[0]
                    else:
                        fix_content = result

                    tracker_mode = getattr(fix_generator, 'output_style', 'full_file')
                    stored_content = diff_output if tracker_mode == "diff" and diff_output is not None else fix_content
                    applied_payload = fix_content if tracker_mode == "diff" else None

                    evaluation_tracker.add_fix(
                        entry['path'],
                        stored_content,
                        mode=tracker_mode,
                        applied_content=applied_payload,
                    )
                    if usage:
                        evaluation_tracker.add_tokens('fix_generation', usage)
                except Exception as gen_err:
                    print(f"[EVAL] Failed to generate fix for {entry['path']}: {gen_err}")

    if remaining_unsure:
        print(f"\nStill uncertain ({len(remaining_unsure)}):")
        for entry in remaining_unsure:
            print(f"  - {entry.path} ({entry.reason})")

    elapsed = time.perf_counter() - analysis_timer_start

    metadata = {
        'provider': config.provider.value,
        'model': config.model_name,
        'root_path': config.root_path,
        'run_started_at': run_started_at.isoformat() + 'Z',
        'elapsed_seconds': round(elapsed, 3),
    }

    if token_totals['total'] == 0:
        derived_total = token_totals['prompt'] + token_totals['completion']
        if derived_total == 0:
            derived_total = token_totals['input'] + token_totals['output']
        token_totals['total'] = derived_total

    result_payload = {
        'metadata': metadata,
        'sure': final_sure,
        'report_entries': report_entries,
        'still_unsure': [u.model_dump() for u in remaining_unsure],
        'refinement_stats': [s.model_dump() for s in refinement_stats],
        'token_usage': token_totals,
    }

    if evaluation_tracker:
        evaluation_tracker.set_results(
            final_sure,
            result_payload['still_unsure'],
            result_payload['refinement_stats'],
        )

    return result_payload


async def run_fix(args) -> Dict[str, Any]:
    """Generate a fix for a single file based on a fresh impact report."""

    if not getattr(args, 'target_path', None):
        raise ValueError("--target-path is required when action is 'fix'")

    load_dotenv()
    config = AgentConfig.from_env(args)

    target_path = resolve_repo_path(config.root_path, args.target_path)
    target_path = normalize_path(target_path)

    context_bundle = _prepare_context(config)

    provider = ProviderFactory.create_provider(config.provider, config.model_name)

    async_manager = AsyncUnifiedChatManager(
        provider=provider,
        api_key=config.api_key,
        tree_json=context_bundle['tree_json'],
        diffs_context=context_bundle['diffs_context'],
        readme_content=context_bundle['readme_content'],
        max_retries=config.max_retries,
        max_workers=4,
    )

    report_entries = await report_detailed_async(
        [target_path],
        async_manager,
        config.root_path,
        evaluation_tracker=None,
    )

    if not report_entries:
        raise ValueError(f"No impact report produced for {target_path}")

    entry = report_entries[0]
    parsed = entry.get('parsed')
    if not parsed:
        raise ValueError("Detailed impact report missing parsed payload; cannot generate fix")

    impact_report = DetailedImpactReport(**parsed)

    sync_manager = UnifiedChatManager(
        provider=provider,
        api_key=config.api_key,
        tree_json=context_bundle['tree_json'],
        diffs_context=context_bundle['diffs_context'],
        readme_content=context_bundle['readme_content'],
        max_retries=config.max_retries,
    )

    fix_generator = CodeFixGenerator(sync_manager, config.root_path)

    result = fix_generator.generate_fix(
        target_path,
        impact_report,
        commit_diff=context_bundle['diffs'],
        include_usage=True,
    )

    fixed_content: str
    usage: Optional[Dict[str, Any]]
    if isinstance(result, tuple):
        fixed_content, usage = result
    else:
        fixed_content = result
        usage = None

    payload = {
        'path': target_path,
        'impact_report': impact_report.model_dump(),
        'fixed_content': fixed_content,
        'usage': usage,
    }

    return payload


def _make_summary(runs: List[Dict]) -> Dict[str, Any]:
    aggregate: Dict[str, Any] = {
        'total_runs': len(runs),
        'files_by_run': {},
        'average_input_tokens': 0.0,
        'average_total_tokens': 0.0,
        'average_total_elapsed_s': 0.0,
        'total_input_tokens': 0,
        'total_elapsed_s': 0.0,
        'repository_token_count': None,
    }

    if not runs:
        return {'runs': runs, 'aggregate': aggregate}

    total_input = 0
    total_tokens = 0
    total_elapsed = 0.0

    for metrics in runs:
        run_label = f"run_{metrics['run_index']}"
        aggregate['files_by_run'][run_label] = metrics.get('final_sure', [])

        total_input += metrics.get('tokens_input_total', 0)
        aggregate_tokens = metrics.get('tokens_aggregate', {})
        total_tokens += aggregate_tokens.get('total', 0)
        total_elapsed += metrics.get('timing', {}).get('total_elapsed_s', 0.0)

    run_count = len(runs)
    aggregate['total_input_tokens'] = total_input
    aggregate['total_elapsed_s'] = total_elapsed
    aggregate['average_input_tokens'] = round(total_input / run_count, 2)
    aggregate['average_total_tokens'] = round(total_tokens / run_count, 2)
    aggregate['average_total_elapsed_s'] = round(total_elapsed / run_count, 3)

    return {'runs': runs, 'aggregate': aggregate}


async def run_evaluations(args):
    runs = max(1, getattr(args, 'evaluation_runs', 5) or 5)
    output_dir = Path(getattr(args, 'evaluation_output', 'evaluation_runs') or 'evaluation_runs')
    summaries = []

    for index in range(runs):
        print(f"\n=== Evaluation Run {index + 1} of {runs} ===")
        tracker = EvaluationTracker(index, output_dir)
        await run_analysis(args, evaluation_tracker=tracker)
        tracker.finalize()
        tracker.write_output()
        summaries.append(tracker.metrics)

    summary_payload = _make_summary(summaries)

    if summaries:
        repo_tokens = await _calculate_repository_tokens(args.root_path)
        summary_payload['aggregate']['repository_token_count'] = repo_tokens

    summary_path = output_dir / "evaluation_summary.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(summary_path, 'w', encoding='utf-8') as handle:
        json.dump(summary_payload, handle, indent=2)

    aggregate = summary_payload['aggregate']
    print("\nEvaluation summary:")
    print(f"  Runs: {aggregate['total_runs']}")
    print(f"  Average input tokens: {aggregate['average_input_tokens']}")
    print(f"  Average total tokens: {aggregate['average_total_tokens']}")
    print(f"  Average elapsed (s): {aggregate['average_total_elapsed_s']}")
    if aggregate['repository_token_count'] is not None:
        print(f"  Repository token count: {aggregate['repository_token_count']}")

    print(f"\nEvaluation artefacts written to {output_dir}")
    return summary_payload


async def _calculate_repository_tokens(root_path: str) -> int:
    """Compute approximate token count for the repository using tiktoken."""
    if not root_path:
        return 0
    encoding = tiktoken.get_encoding("cl100k_base")
    token_count = 0
    root = Path(root_path)
    if not root.exists():
        return 0

    for path in root.rglob('*'):
        if path.is_file():
            try:
                text = path.read_text(encoding='utf-8')
            except Exception:
                continue
            token_count += len(encoding.encode(text))
    return token_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Artifact Sync Agent")

    parser.add_argument('--provider', type=str, help="Model provider (e.g., 'GEMINI', 'OPENAI', 'ANTHROPIC')")
    parser.add_argument('--model_name', type=str, help="The specific model name to use")
    parser.add_argument('--api_key', type=str, help="API key for the selected provider")
    parser.add_argument('--root_path', type=str, help="The root path of the repository to analyze")
    parser.add_argument('--max_depth', type=int, help="Maximum depth for directory traversal")
    parser.add_argument('--max_retries', type=int, help="Maximum retries for API calls")
    parser.add_argument('--action', choices=['analyze', 'fix'], default='analyze', help="Operation to perform (default: analyze)")
    parser.add_argument('--target-path', type=str, help="Target file (relative to root_path) when generating a fix")
    parser.add_argument('--evaluation', action='store_true', help="Enable evaluation mode with multi-run instrumentation")
    parser.add_argument('--evaluation-runs', type=int, default=5, help="Number of runs to execute in evaluation mode (default: 5)")
    parser.add_argument('--evaluation-output', type=str, default='evaluation_runs', help="Directory to store evaluation artefacts")
    parser.add_argument('--output-format', choices=['human', 'json'], default='human', help="Format for stdout output (default: human)")

    args = parser.parse_args()

    def _execute(parsed_args):
        if getattr(parsed_args, 'evaluation', False):
            return asyncio.run(run_evaluations(parsed_args))

        action = getattr(parsed_args, 'action', 'analyze')
        if action == 'fix':
            return asyncio.run(run_fix(parsed_args))
        return asyncio.run(run_analysis(parsed_args))

    if args.output_format == 'json':
        with _PrintRedirector(sys.stderr):
            try:
                result = _execute(args)
                payload = {'status': 'success', 'data': result}
                json.dump(payload, sys.stdout)
                sys.stdout.write('\n')
                sys.stdout.flush()
            except Exception as exc:
                traceback.print_exc()
                error_payload = {'status': 'error', 'message': str(exc)}
                json.dump(error_payload, sys.stdout)
                sys.stdout.write('\n')
                sys.stdout.flush()
    else:
        _execute(args)
