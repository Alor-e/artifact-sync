from pathlib import Path
from typing import Dict, Any, List, Union, Optional
from utils.git_ignore_handler import GitIgnoreProcessor, create_git_ignore_processor

def build_tree(
    root: Union[str, Path],
    max_depth: int = 3,
    exclude_dirs: List[str] = None,
    git_ignore_processor: Optional[GitIgnoreProcessor] = None,
    _level: int = 0,
    _relative_base: str = ""
) -> Dict[str, Any]:
    """
    Recursively builds a nested dict representing the file tree:
      { name: { child_name: {...}, ... }, files: [file1, file2, ...] }
    Excludes any top-level dirs in exclude_dirs and respects .gitignore rules.
    
    Args:
        root: Root directory to scan
        max_depth: Maximum depth to scan
        exclude_dirs: Additional directories to exclude (beyond .gitignore)
        git_ignore_processor: GitIgnore processor instance (auto-created if None)
        _level: Internal recursion level tracker
        _relative_base: Internal relative path tracker for gitignore context
    """
    if exclude_dirs is None:
        exclude_dirs = [".git"]
    
    root = Path(root).resolve()
    
    # Create gitignore processor on first call if not provided
    if git_ignore_processor is None and _level == 0:
        git_ignore_processor = create_git_ignore_processor(root)
        if git_ignore_processor:
            print("[TREE] Using .gitignore filtering")
        else:
            print("[TREE] No .gitignore filtering (not a git repo or no .gitignore files)")
    
    tree: Dict[str, Any] = {
        "name": root.name,
        "dirs": {},
        "files": [],
        "depth": _level
    }

    if _level > max_depth:
        tree["truncated"] = True
        return tree

    try:
        entries = sorted(root.iterdir(), key=lambda e: e.name.lower())
    except PermissionError:
        print(f"[TREE] Permission denied accessing {root}")
        tree["truncated"] = True
        tree["error"] = "Permission denied"
        return tree
    except Exception as e:
        print(f"[TREE] Error accessing {root}: {e}")
        tree["truncated"] = True
        tree["error"] = str(e)
        return tree

    for entry in entries:
        entry_name = entry.name
        
        # Skip explicitly excluded directories
        if entry_name in exclude_dirs:
            continue
        
        # Calculate relative path for gitignore checking
        if _relative_base:
            relative_path = f"{_relative_base}/{entry_name}"
        else:
            relative_path = entry_name
        
        # Check gitignore rules
        if git_ignore_processor:
            is_directory = entry.is_dir()
            should_ignore = git_ignore_processor.should_ignore(
                relative_path, 
                is_directory, 
                _relative_base
            )
            
            if should_ignore:
                continue
        
        if entry.is_dir():
            # Recursively build subtree
            subtree = build_tree(
                entry, 
                max_depth, 
                exclude_dirs, 
                git_ignore_processor,
                _level + 1,
                relative_path
            )
            tree["dirs"][entry_name] = subtree
        else:
            tree["files"].append({
                "name": entry_name,
                "depth": _level
            })

    if _level > max_depth:
        tree["truncated"] = True
    
    return tree
