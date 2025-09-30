from git import Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError
from typing import Dict, Any, Optional
import json, argparse

def get_diffs(repo_path: str, rev: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns enhanced diff information with both structured metadata and raw patches.
    Provides multiple formats for different analysis needs.
    """
    try:
        repo = Repo(repo_path)
    except (InvalidGitRepositoryError, NoSuchPathError):
        print(f"[DIFF] Warning: {repo_path} is not a git repository; using empty diff context")
        return _empty_diff_payload()

    try:
        commit = repo.commit(rev) if rev else repo.head.commit
    except Exception:
        print(f"[DIFF] Warning: Unable to resolve commit for {repo_path}; using empty diff context")
        return _empty_diff_payload()
    parent = commit.parents[0] if commit.parents else None
    diffs = parent.diff(commit, create_patch=True) if parent else commit.diff(None, create_patch=True)

    structured_diffs = []
    raw_patches = []
    file_changes_summary = {
        'added': [],
        'deleted': [],
        'modified': [],
        'renamed': []
    }
    
    for d in diffs:
        change_type = (
            "added" if d.new_file
            else "deleted" if d.deleted_file
            else "renamed" if d.renamed_file
            else "modified"
        )
        
        path = d.b_path or d.a_path or ""
        
        # Structured metadata
        diff_entry = {
            "path": path,
            "change_type": change_type,
            "old_path": d.a_path if d.renamed_file else None,
            "new_path": d.b_path if d.renamed_file else None,
            "insertions": d.stats.insertions if hasattr(d, 'stats') else None,
            "deletions": d.stats.deletions if hasattr(d, 'stats') else None,
        }
        structured_diffs.append(diff_entry)
        
        # Raw patch content (more readable for LLMs)
        patch_content = d.diff.decode(errors="ignore")
        raw_patches.append({
            "path": path,
            "patch": patch_content
        })
        
        # Summary categorization
        file_changes_summary[change_type].append(path)
    
    return {
        "commit_info": {
            "sha": getattr(commit, "hexsha", ""),
            "message": (commit.message.strip() if getattr(commit, "message", None) else ""),
            "author": str(getattr(commit, "author", "")),
            "date": getattr(commit, "committed_date", 0)
        },
        "summary": {
            "total_files_changed": len(structured_diffs),
            "files_by_type": file_changes_summary
        },
        "structured_diffs": structured_diffs,
        "raw_patches": raw_patches
    }


def _empty_diff_payload() -> Dict[str, Any]:
    """Return a placeholder diff payload when git metadata is unavailable."""
    return {
        "commit_info": {
            "sha": "",
            "message": "",
            "author": "",
            "date": 0
        },
        "summary": {
            "total_files_changed": 0,
            "files_by_type": {
                "added": [],
                "deleted": [],
                "modified": [],
                "renamed": []
            }
        },
        "structured_diffs": [],
        "raw_patches": []
    }

def create_diff_context(enhanced_diffs: Dict[str, Any]) -> str:
    """
    Create different context formats based on analysis needs.
    
    Args:
        enhanced_diffs: Output from get_enhanced_diffs()
        format_style: "structured", "raw", or "hybrid"
    """
    summary = enhanced_diffs.get('summary', {})
    files_by_type = summary.get('files_by_type', {
        'added': [],
        'deleted': [],
        'modified': [],
        'renamed': []
    })
    commit_info = enhanced_diffs.get('commit_info', {})
    commit_sha = (commit_info.get('sha') or '')[:8]
    commit_message = commit_info.get('message', '')
    total_changed = summary.get('total_files_changed', 0)
    raw_patches = enhanced_diffs.get('raw_patches', [])
    detailed = chr(10).join([f"=== {patch.get('path', '')} ==={chr(10)}{patch.get('patch', '')}" for patch in raw_patches])
    return f"""
Git Commit Analysis:
Commit: {commit_sha}
Message: {commit_message}

Summary: {total_changed} files changed
{f"Added: {', '.join(files_by_type['added'])}" if files_by_type.get('added') else ""}
{f"Modified: {', '.join(files_by_type['modified'])}" if files_by_type.get('modified') else ""}
{f"Deleted: {', '.join(files_by_type['deleted'])}" if files_by_type.get('deleted') else ""}
{f"Renamed: {', '.join(files_by_type['renamed'])}" if files_by_type.get('renamed') else ""}

Detailed Changes:
{detailed}
"""
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Print enhanced git-diff from any directory by specifying the repo root"
    )
    parser.add_argument(
        "-d", "--repo-dir",
        required=True,
        help="Path to the root of the Git repository"
    )
    parser.add_argument(
        "-r", "--rev",
        help="Commit SHA or ref to diff against its parent",
        default=None
    )
    args = parser.parse_args()

    diffs = get_diffs(args.repo_dir, args.rev)
    print(create_diff_context(diffs))
