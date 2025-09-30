import os
import re
import json
import argparse
from dotenv import load_dotenv
from typing import Optional, Dict, Any, Tuple, Union, Literal
from core.schemas import DetailedImpactReport, ChatConfig
from core.abstractions import ChatSession, ModelProvider
from llm.manager import UnifiedChatManager
from llm.providers.base import ProviderFactory
from context.tree_getter import build_tree
from context.diff_getter import get_diffs
from utils.git_ignore_handler import create_git_ignore_processor
from utils.helpers import safe_join_path, resolve_repo_path

class CodeFixGenerator:
    """Generates code fixes based on detailed impact analysis"""
    
    def __init__(
        self,
        chat_manager: UnifiedChatManager,
        root_path: str,
        *,
        output_style: Literal["full_file", "diff"] = "full_file"
    ):
        self.chat_manager = chat_manager
        self.root_path = root_path
        self._fix_chat: Optional[ChatSession] = None
        if output_style not in {"full_file", "diff"}:
            raise ValueError(f"Unsupported output style: {output_style}")
        self.output_style = output_style
    
    def _build_system_instruction(self) -> str:
        base_context = self.chat_manager._create_base_context()

        if self.output_style == "diff":
            return f"""
        You are a code fix generation expert. Your role is to produce precise unified diff patches that resolve the issues identified by analysis.

        Key principles:
        - Emit a unified diff for a single file using standard `---`/`+++` headers and `@@` hunks
        - Include only the minimal hunks required to implement the fix (no unrelated edits)
        - Keep context lines around modifications so the patch can be applied cleanly
        - Never add explanations, commentary, or extra code fences outside the diff
        - Preserve existing style and functionality while addressing the recommendations

        Repository Context:
        {base_context}
        """

        return f"""
        You are a code fix generation expert. Your role is to generate corrected code based on:
        1. The original file content
        2. A detailed impact analysis report
        3. The commit diff that caused the impact

        Your task is to produce a complete, corrected version of the file that addresses the issues identified in the impact analysis.

        Key principles:
        - Generate complete, working code (not just snippets or patches)
        - Follow the existing code style and patterns
        - Address all issues mentioned in the impact analysis recommendations
        - Preserve existing functionality while fixing the identified issues
        - Maintain proper error handling and edge cases

        Always return the COMPLETE file content with all fixes applied.
        Do not include explanations or comments about the changes unless they're part of the actual code.

        Repository Context:
        {base_context}
        """

    def _get_fix_chat(self) -> ChatSession:
        """Get or create the fix generation chat with specialized context"""
        if self._fix_chat is not None:
            return self._fix_chat
        
        print("[CONTEXT] Creating fix generation chat")
        
        config = ChatConfig(
            system_instruction=self._build_system_instruction(),
            temperature=0.1,
            response_format="text",
            response_schema=None
        )
        
        self._fix_chat = self.chat_manager._create_chat_with_retry(config)
        return self._fix_chat
    
    def generate_fix(self, 
                    file_path: str, 
                    impact_report: DetailedImpactReport,
                    commit_diff: Optional[Dict[str, Any]] = None,
                    *,
                    include_usage: bool = False) -> Union[str, Tuple[str, Optional[Dict[str, Any]]]]:
        """
        Generate a fixed version of the code file.
        """
        
        if not impact_report.diagnosis.needs_update:
            raise ValueError(f"File {file_path} does not need updates according to impact analysis")
        
        requested_path = file_path
        resolved_path = resolve_repo_path(self.root_path, file_path)

        if resolved_path != file_path:
            print(f"[FIX] Resolved path {file_path} -> {resolved_path}")
            file_path = resolved_path
            if hasattr(impact_report, 'model_dump'):
                updated = impact_report.model_dump()
                updated['path'] = file_path
                impact_report = DetailedImpactReport(**updated)

        full_path = safe_join_path(self.root_path, file_path)
        
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
        except Exception as e:
            raise FileNotFoundError(f"Could not read file {file_path}: {e}")
        
        prompt = self._build_fix_prompt(file_path, original_content, impact_report, commit_diff, requested_path)
        
        print(f"[FIX] Generating fix for {file_path}")
        chat = self._get_fix_chat()
        
        try:
            response = chat.send_message(prompt)
            raw_output = response.content.strip()

            diff_output: Optional[str] = None

            if self.output_style == "diff":
                diff_output = self._extract_diff_content(raw_output)
                fixed_content = self._apply_unified_diff(original_content, diff_output)
            else:
                fixed_content = raw_output
            
            if self.output_style == "full_file":
                # Basic validation - ensure we got actual code back
                if not fixed_content or len(fixed_content) < 10:
                    raise ValueError(f"Generated fix appears to be empty or too short for {file_path}")

            if include_usage:
                if self.output_style == "diff":
                    return fixed_content, response.usage, diff_output
                return fixed_content, response.usage
            return fixed_content
            
        except Exception as e:
            raise RuntimeError(f"Failed to generate fix for {file_path}: {e}")
    
    def _build_fix_prompt(self, 
                         file_path: str, 
                         original_content: str, 
                         impact_report: DetailedImpactReport,
                         commit_diff: Optional[Dict[str, Any]],
                         requested_path: Optional[str]) -> str:
        """Build the prompt for fix generation"""

        impact_summary = f"""
        Impact Analysis:
        - Type: {impact_report.analysis.impact}
        - Description: {impact_report.analysis.impact_description}
        - Needs Update: {impact_report.diagnosis.needs_update}
        - Rationale: {impact_report.diagnosis.update_rationale}
        - Recommended Actions: {', '.join(impact_report.recommendations.recommended_actions)}
        """

        diff_section = self._build_diff_section(commit_diff, file_path, requested_path)
        requirements = self._build_requirements_block(file_path)

        prompt = f"""
        File to fix: {file_path}

        {impact_summary}

        {diff_section}

        Original File Content:
        ```
        {original_content}
        ```

        {requirements}

        Produce your answer now:
        """

        return prompt

    def _build_requirements_block(self, file_path: str) -> str:
        if self.output_style == "diff":
            return """
        Requirements:
        1. Return a unified diff patch for this exact file only (`{file_path}`)
        2. Use standard headers (`--- {file_path}` / `+++ {file_path}`) and include `@@` hunks with context
        3. Modify only the lines needed to implement the recommendations
        4. Do not include explanations, commentary, or surrounding prose
        5. If no change is required, return an empty diff showing no hunks
            """.format(file_path=file_path)

        return """
        Requirements:
        1. Address all issues identified in the recommended actions
        2. Ensure the code is syntactically correct and functional
        3. Maintain the existing code structure and style
        4. Include all necessary imports and dependencies
        5. Preserve existing functionality while fixing the identified issues
        6. Return ONLY the complete corrected file content (no explanations)
        """

    def _build_diff_section(self, commit_diff: Optional[Dict[str, Any]], file_path: str, alternate_path: Optional[str]) -> str:
        if not commit_diff:
            return ""

        patch = self._extract_patch_for_file(commit_diff, file_path)
        if not patch and alternate_path and alternate_path != file_path:
            patch = self._extract_patch_for_file(commit_diff, alternate_path)
        if not patch:
            return ""

        fence = "diff" if not patch.lstrip().startswith("{") else "json"
        return f"""
        Relevant Commit Diff:
        ```{fence}
        {patch}
        ```
        """

    def _extract_patch_for_file(self, commit_diff: Optional[Dict[str, Any]], file_path: str) -> Optional[str]:
        if commit_diff is None:
            return None

        diff_data: Any = commit_diff
        if isinstance(commit_diff, str):
            try:
                diff_data = json.loads(commit_diff)
            except json.JSONDecodeError:
                # If we were given a raw string diff, surface it directly
                return commit_diff

        if isinstance(diff_data, dict):
            raw_patches = diff_data.get('raw_patches')
            if isinstance(raw_patches, list):
                for patch in raw_patches:
                    if isinstance(patch, dict) and patch.get('path') == file_path:
                        return patch.get('patch')

        return None

    def _extract_diff_content(self, raw_output: str) -> str:
        """Normalize the model output to a raw unified diff string."""
        text = raw_output.strip()
        if not text:
            return ""

        if text.startswith("```"):
            lines = text.splitlines()
            closing_index = None
            for idx, line in enumerate(lines[1:], start=1):
                if line.strip().startswith("```"):
                    closing_index = idx
                    break
            if closing_index is not None:
                text = "\n".join(lines[1:closing_index])
            else:
                text = "\n".join(lines[1:])

        cleaned_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped and cleaned_lines and cleaned_lines[-1] == "":
                continue
            if stripped.startswith("diff --git") or stripped.startswith("index "):
                continue
            cleaned_lines.append(line)

        normalized = "\n".join(cleaned_lines).strip()

        if normalized and ('---' not in normalized or '+++' not in normalized):
            raise ValueError("Generated diff is missing required headers")

        return normalized

    def _apply_unified_diff(self, original_content: str, diff_text: str) -> str:
        """Apply a unified diff string to the original content."""
        if not diff_text:
            return original_content

        diff_lines = diff_text.splitlines()
        if not any(line.startswith('@@') for line in diff_lines):
            # Allow empty diff (headers only)
            if '---' in diff_text and '+++' in diff_text:
                return original_content
            raise ValueError("Diff output does not contain any hunks")

        original_lines = original_content.splitlines(keepends=True)
        patched_lines = []
        orig_index = 0

        i = 0
        while i < len(diff_lines):
            line = diff_lines[i]

            if line.startswith('---') or line.startswith('+++'):
                i += 1
                continue

            if line.startswith('@@'):
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if match:
                    start_line = int(match.group(1))
                    hunk_start = max(start_line, 1) - 1
                    if hunk_start < orig_index:
                        raise ValueError("Diff hunk overlaps with previous content")
                    patched_lines.extend(original_lines[orig_index:hunk_start])
                    orig_index = hunk_start
                i += 1

                while i < len(diff_lines):
                    hunk_line = diff_lines[i]
                    if hunk_line.startswith('@@'):
                        break
                    if hunk_line.startswith('---') or hunk_line.startswith('+++'):
                        i += 1
                        continue
                    if hunk_line.startswith('\\ No newline at end of file'):
                        # Respect lack of trailing newline by trimming last appended line
                        if patched_lines:
                            patched_lines[-1] = patched_lines[-1].rstrip('\n')
                        i += 1
                        continue

                    if hunk_line.startswith(' '):
                        if orig_index >= len(original_lines):
                            raise ValueError("Context line exceeds original file length")
                        patched_lines.append(original_lines[orig_index])
                        orig_index += 1
                    elif hunk_line.startswith('-'):
                        orig_index += 1
                    elif hunk_line.startswith('+'):
                        addition = hunk_line[1:]
                        patched_lines.append(addition + '\n')
                    elif not hunk_line.strip():
                        patched_lines.append('\n')
                    else:
                        raise ValueError(f"Unrecognized diff line: {hunk_line}")
                    i += 1
                continue

            i += 1

        patched_lines.extend(original_lines[orig_index:])
        result = ''.join(patched_lines)
        return result

def create_fix_generator(
    chat_manager: UnifiedChatManager,
    root_path: str,
    *,
    output_style: Literal["full_file", "diff"] = "full_file"
) -> CodeFixGenerator:
    """Factory function to create a CodeFixGenerator instance"""
    return CodeFixGenerator(chat_manager, root_path, output_style=output_style)

def generate_single_fix(file_path: str, 
                       impact_report: DetailedImpactReport,
                       chat_manager: UnifiedChatManager,
                       root_path: str,
                       *,
                       output_style: Literal["full_file", "diff"] = "full_file") -> Dict[str, Any]:
    """
    High-level function to generate a fix for a single file.
    """
    
    generator = create_fix_generator(chat_manager, root_path, output_style=output_style)

    try:
        fixed_content = generator.generate_fix(file_path, impact_report)
        
        result = {
            "file_path": file_path,
            "fixed_content": fixed_content,
            "success": True,
            "error": None
        }
        
        return result
        
    except Exception as e:
        return {
            "file_path": file_path,
            "fixed_content": None,
            "success": False,
            "error": str(e),
            "validation": None
        }
    
if __name__ == '__main__':
    load_dotenv()
    parser = argparse.ArgumentParser(description="Generate a code fix for a single file based on impact report")

    parser.add_argument('--provider', type=str, choices=[p.value.lower() for p in ModelProvider], required=True,
        help="Model provider (gemini, openai, anthropic)"
    )
    parser.add_argument('--model_name', type=str, help="The specific model name to use", required=True)
    parser.add_argument('--api_key', type=str, help="API key for the selected provider", required=True)
    parser.add_argument('--root_path', type=str, help="The root path of the repository to analyze", required=True)
    parser.add_argument('--report', type=str, help="Path to JSON file with DetailedImpactReport data", required=True)
    parser.add_argument('--output_style', type=str, choices=['full_file', 'diff'], default='full_file',
        help="Return full file content (default) or unified diff patches"
    )
    args = parser.parse_args()

    # Convert provider string to enum
    try:
        provider_enum = ModelProvider(args.provider.upper())
    except ValueError:
        raise ValueError(f"Invalid provider specified: {args.provider}")

    with open(args.report, 'r', encoding='utf-8') as f:
        report_data = json.load(f)
    impact_report = DetailedImpactReport(**report_data)

    # Build context
    git_ignore = create_git_ignore_processor(args.root_path)
    tree = build_tree(args.root_path, 3, exclude_dirs=None, git_ignore_processor=git_ignore)
    diffs = get_diffs(args.root_path)
    tree_json = json.dumps(tree, indent=2)
    diffs_json = json.dumps(diffs, indent=2)

    # Initialize chat manager
    provider = ProviderFactory.create_provider(provider_enum, args.model_name)
    chat_manager = UnifiedChatManager(
        provider=provider,
        api_key=args.api_key,
        tree_json=tree_json,
        diffs_json=diffs_json,
        max_retries=3
    )

    # Generate and output result
    generator = create_fix_generator(chat_manager, args.root_path, output_style=args.output_style)
    result = generator.generate_fix(impact_report.path, impact_report, diffs)
    print(result)
