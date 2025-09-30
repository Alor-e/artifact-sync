import asyncio
from typing import List, Tuple, Optional, Any
from core.schemas import UnsureEntry, RefinementDecision, RefineStats
from llm.manager import AsyncUnifiedChatManager
from analysis.subfolder import analyze_subfolder_async
from utils.helpers import resolve_needed_info_async, is_folder_at_max_depth, resolve_repo_path
from utils.git_ignore_handler import GitIgnoreProcessor
    
async def refine_entry_async(
    entry: UnsureEntry,
    root_path: str,
    chat_manager: AsyncUnifiedChatManager,
    evaluation_tracker: Optional[Any] = None
) -> RefinementDecision:
    """Async version of refine_entry with pruning-focused approach"""
    resolved_path = resolve_repo_path(root_path, entry.path)
    if resolved_path != entry.path:
        print(f"[PATH] Resolved refine target {entry.path} -> {resolved_path}")
        try:
            entry.path = resolved_path
        except Exception:
            entry = UnsureEntry(
                path=resolved_path,
                is_dir=entry.is_dir,
                reason=entry.reason,
                needed_info=entry.needed_info
            )

    extra = await resolve_needed_info_async(entry, root_path, chat_manager.executor)
    
    if extra.get('type') == 'directory':
        print(f"[DIRECTORY] {entry.path} is a directory - scheduling for subfolder analysis")
        return RefinementDecision(
            path=entry.path,
            related=True,
            confidence="high",
            reasoning="Directory detected - should be analyzed as subfolder"
        )
    
    if extra.get('type') == 'error':
        print(f"[ERROR] Could not process {entry.path}: {extra.get('error')}")
        # Errors get low confidence and will be pruned
        return RefinementDecision(
            path=entry.path,
            related=False,
            confidence="low",
            reasoning=f"Error accessing file: {extra.get('error')} - assuming not impacted"
        )
    
    # Build info block with truncation awareness
    if extra['type'] == 'overview':
        info_block = "Function/class headers:\n" + "\n".join(f"- {h}" for h in extra['entries'])
        info_level = "function/class overview"
    else:
        text = extra['text']
        info_block = f"Raw content:\n```text\n{text}\n```"
        info_level = "full raw content"
        
        if extra.get('truncated', False):
            original_size = extra.get('original_size', 0)
            info_block += f"\n... (truncated at {len(text)} chars, full file is {original_size} chars)"

    # Determine decision guidance based on information level
    is_final_step = entry.needed_info == 'raw_content'
    
    guidance = ""
    if is_final_step:
        guidance = """
        FINAL DECISION REQUIRED
        This is raw content analysis, you MUST make a final decision.
        No further escalation is possible. Choose "related: true" or "related: false" decisively.
        """
    else:
        guidance = """
        OVERVIEW ANALYSIS
        This is function/class overview analysis. If you need more detail, you can use "low" confidence
        to request raw content analysis in the next iteration.
        """

    # Create refinement prompt
    prompt = f"""
        File: {entry.path}
        Analysis Level: {info_level}
        Original Reason: {entry.reason}

        {info_block}

        {guidance}

        Make your decision on whether this file is related/impacted by the changes:

        Respond with JSON including:
        - path: the file path
        - related: true if the file is impacted, false if not  
        - confidence: "high", "medium", or "low"
        - reasoning: brief explanation of your decision

        Key decision criteria:
        - If you have enough information to make a confident decision (either way), set confidence to "high"
        - For overview analysis: use "low" confidence only if you genuinely need to see raw content
        - It's okay to confidently determine a file is NOT related
        - It's better to be decisive than uncertain
    """

    try:
        refinement_chat = chat_manager.get_async_chat("refinement")
        response = await refinement_chat.send_message_async(prompt)

        if evaluation_tracker:
            evaluation_tracker.add_tokens('refinement', response.usage)
        decision: RefinementDecision = response.parsed
        
        return decision
        
    except Exception as e:
        print(f"Error refining {entry.path}: {e}")
        # Return low confidence so it gets pruned
        return RefinementDecision(
            path=entry.path,
            related=False,
            confidence="low",
            reasoning=f"Error during refinement: {e} - pruning as not impacted"
        )
    
async def process_refinement_batch(
    entries: List[UnsureEntry],
    root_path: str,
    chat_manager: AsyncUnifiedChatManager,
    max_depth: int,
    git_ignore_processor: Optional[GitIgnoreProcessor],
    evaluation_tracker: Optional[Any] = None,
) -> Tuple[List[str], List[UnsureEntry], List[str], List[RefineStats]]:
    """Process a batch of refinement entries in parallel"""
    
    print(f"[PARALLEL] Processing {len(entries)} entries in parallel...")
    
    # Log initial info levels for each entry
    info_level_summary = {}
    for entry in entries:
        level = entry.needed_info if not entry.is_dir else "directory"
        info_level_summary[level] = info_level_summary.get(level, 0) + 1
    
    print(f"[INFO_LEVELS] {dict(info_level_summary)}")
    
    # Create tasks for parallel processing
    tasks = []
    for entry in entries:
        if entry.is_dir and is_folder_at_max_depth(entry.path, root_path, max_depth):
            # Handle folder analysis
            task = analyze_subfolder_async(entry.path, root_path, chat_manager, git_ignore_processor, max_depth, evaluation_tracker)
            tasks.append(('folder', entry.path, task))
            print(f"[TASK] {entry.path} -> folder analysis")
        else:
            # Handle file refinement
            task = refine_entry_async(entry, root_path, chat_manager, evaluation_tracker)
            tasks.append(('file', entry, task))
            print(f"[TASK] {entry.path} -> {entry.needed_info} analysis")
    
    results = await asyncio.gather(*[task for _, _, task in tasks], return_exceptions=True)
    
    # Process results
    final_sure = []
    remaining_unsure = []
    folders_to_analyze = []
    refinement_stats = []
    
    for i, ((task_type, item, _), result) in enumerate(zip(tasks, results)):
        if isinstance(result, Exception):
            print(f"[ERROR] Task failed: {result}")
            if task_type == 'file':
                # Conservative fallback for failed refinements
                remaining_unsure.append(item)
            continue
        
        if task_type == 'folder':
            folder_path = item
            sure_files, unsure_entries = result
            final_sure.extend(sure_files)
            remaining_unsure.extend(unsure_entries)
            print(f"[FOLDER_RESULT] {folder_path}: {len(sure_files)} sure, {len(unsure_entries)} unsure")
            
        elif task_type == 'file':
            entry = item
            decision: RefinementDecision = result
            
            print(f"[DECISION] {entry.path}: {decision.related} ({decision.confidence}) [was: {entry.needed_info}]")
            
            stats = RefineStats(
                path=entry.path,
                decision="related" if decision.related else "not_related",
                confidence=decision.confidence,
                reasoning=decision.reasoning
            )
            refinement_stats.append(stats)
            
            # Check if decision indicates this is actually a directory
            if "Directory detected" in decision.reasoning:
                folders_to_analyze.append(entry.path)
                print(f"[REDIRECT] {entry.path} -> discovered to be directory")
                continue
            
            # Standard decision processing with escalation tracking
            if decision.related:
                final_sure.append(entry.path)
                print(f"[SURE] {entry.path} -> added to final results")
            else:
                print(f"[NOT_RELATED] {entry.path} -> excluded from results")
            
            # Track if this will need escalation (low confidence)
            if decision.confidence == "low":
                print(f"[LOW_CONF] {entry.path} -> low confidence, will be escalated or pruned")
    
    return final_sure, remaining_unsure, folders_to_analyze, refinement_stats
