import os
import json
import asyncio
from typing import List, Tuple, Optional, Any
from core.schemas import UnsureEntry, AnalysisResult
from llm.manager import AsyncUnifiedChatManager
from context.tree_getter import build_tree
from utils.helpers import normalize_path, safe_join_path, resolve_repo_path
from utils.git_ignore_handler import GitIgnoreProcessor
    
async def analyze_subfolder_async(
    folder_path: str,
    root_path: str,
    chat_manager: AsyncUnifiedChatManager,
    git_ignore_processor: Optional[GitIgnoreProcessor],
    max_depth: int = 3,
    evaluation_tracker: Optional[Any] = None
) -> Tuple[List[str], List[UnsureEntry]]:
    """Async version of analyze_subfolder"""
    print(f"[SUBFOLDER] Analyzing contents of {folder_path}")
    
    # Build absolute path for the subfolder using safe path joining
    if os.path.isabs(folder_path):
        sub_root = normalize_path(folder_path)
    else:
        sub_root = safe_join_path(root_path, folder_path)
    
    # Error handling
    if not os.path.exists(sub_root):
        print(f"[ERROR] Subfolder {folder_path} does not exist")
        return [], []
    
    if not os.path.isdir(sub_root):
        print(f"[ERROR] Path {folder_path} is not a directory")
        return [], []
    
    try:
        # Import here to avoid circular imports - run in thread to avoid blocking
        loop = asyncio.get_event_loop()
        
        def build_tree_sync():
            return build_tree(sub_root, max_depth=max_depth, exclude_dirs=None, git_ignore_processor=git_ignore_processor)
        
        subtree = await loop.run_in_executor(chat_manager.executor, build_tree_sync)
        
        # Check if subfolder has any content
        if not subtree.get('dirs') and not subtree.get('files'):
            print(f"[SUBFOLDER] {folder_path} is empty - skipping analysis")
            return [], []
        
        # Get the async subfolder chat
        sub_chat = chat_manager.get_async_chat(f"subfolder:{folder_path}")
        
        # Create focused prompt for subfolder analysis
        sub_tree_json = json.dumps(subtree, indent=2)
        sub_prompt = f"""
            Analyzing subfolder: {folder_path}

            Subfolder structure:
            ```json
            {sub_tree_json}
            ```

            Based on the global repository changes (which you have in context), identify files/folders within this specific subfolder that are impacted or related considering the commit delta.

            Return paths relative to this subfolder (not absolute paths).

            Focus on finding ALL potential impacts, not just direct ones.
        """
        
        # Send analysis request asynchronously
        print(f"[SUBFOLDER] Analyzing {folder_path}")
        sub_resp = await sub_chat.send_message_async(sub_prompt)

        if evaluation_tracker:
            evaluation_tracker.add_tokens('refinement', sub_resp.usage)
        sub_result: AnalysisResult = sub_resp.parsed
        
        # Convert relative paths back to paths relative to original root
        sure_files = []
        for rel_path in sub_result.sure:
            if folder_path == ".":
                combined = normalize_path(rel_path)
            else:
                combined = normalize_path(os.path.join(folder_path, rel_path))
            resolved = resolve_repo_path(root_path, combined)
            if resolved != combined:
                print(f"[PATH] Resolved subfolder sure {combined} -> {resolved}")
            sure_files.append(resolved)

        unsure_entries = []
        for entry in sub_result.unsure:
            if folder_path == ".":
                combined = normalize_path(entry.path)
            else:
                combined = normalize_path(os.path.join(folder_path, entry.path))

            resolved = resolve_repo_path(root_path, combined)
            if resolved != combined:
                print(f"[PATH] Resolved subfolder unsure {combined} -> {resolved}")

            new_entry = UnsureEntry(
                path=resolved,
                is_dir=entry.is_dir,
                reason=entry.reason,
                needed_info=entry.needed_info
            )
            unsure_entries.append(new_entry)
        
        print(f"[SUBFOLDER] {folder_path}: Found {len(sure_files)} sure, {len(unsure_entries)} unsure")
        return sure_files, unsure_entries
        
    except Exception as e:
        print(f"[ERROR] Error analyzing subfolder {folder_path}: {e}")
        return [], []
