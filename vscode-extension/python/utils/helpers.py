import os
import asyncio
from pathlib import Path
from difflib import SequenceMatcher
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from core.schemas import UnsureEntry
from parser.dispatcher import extract_file_overview

def normalize_path(path: str) -> str:
    """Normalize path separators for the current platform"""
    return os.path.normpath(path)

def safe_join_path(base: str, *paths: str) -> str:
    """Safely join paths and normalize separators"""
    return normalize_path(os.path.join(base, *paths))


@lru_cache(maxsize=1024)
def resolve_repo_path(root_path: str, candidate_path: str) -> str:
    """Return a repository-relative path that actually exists on disk."""
    root_norm = normalize_path(root_path)
    normalized = normalize_path(candidate_path)
    full_path = safe_join_path(root_norm, normalized)
    if os.path.exists(full_path):
        return normalized

    trimmed = normalized.lstrip("./")
    trimmed_full = safe_join_path(root_norm, trimmed)
    if os.path.exists(trimmed_full):
        return trimmed

    # Heuristic: collapse consecutive duplicate path segments (e.g., "server/server")
    segments = [seg for seg in normalized.split(os.sep) if seg and seg != '.']
    if segments:
        dedup_segments = []
        for seg in segments:
            if dedup_segments and dedup_segments[-1] == seg:
                continue
            dedup_segments.append(seg)

        if dedup_segments != segments:
            dedup_path = os.sep.join(dedup_segments)
            dedup_full = safe_join_path(root_norm, dedup_path)
            if os.path.exists(dedup_full):
                return dedup_path
            trimmed_dedup = dedup_path.lstrip("./")
            dedup_trimmed_full = safe_join_path(root_norm, trimmed_dedup)
            if os.path.exists(dedup_trimmed_full):
                return trimmed_dedup

    basename = os.path.basename(normalized)
    if not basename:
        return normalized

    try:
        matches = list(Path(root_norm).rglob(basename))
    except OSError:
        matches = []

    if not matches:
        return normalized

    best_match = normalized
    best_score = -1.0

    for match in matches:
        try:
            rel = normalize_path(os.path.relpath(match, root_norm))
        except ValueError:
            continue
        score = SequenceMatcher(None, normalized, rel).ratio()
        if score > best_score:
            best_match = rel
            best_score = score

    return best_match

def compute_path_depth(path: str, root: str) -> int:
    """Calculate the depth of a path relative to root"""
    # Normalize both paths first
    path = normalize_path(path)
    root = normalize_path(root)
    rel = os.path.relpath(path, root)
    if rel == ".":
        return 0
    return rel.count(os.sep) + 1

def is_folder_at_max_depth(path: str, root_path: str, max_depth: int) -> bool:
    """Check if a folder is at the maximum allowed depth"""
    if os.path.isabs(path):
        full_path = normalize_path(path)
    else:
        full_path = safe_join_path(root_path, path)
    return os.path.isdir(full_path) and compute_path_depth(path, root_path) == max_depth

def safe_file_overview(path: str, max_chars: int = 6000) -> dict:
    """
    Robust file overview extraction with proper fallback handling.
    Returns either:
      - { type: 'overview', entries: [<header strings>] }
      - { type: 'raw', text: <truncated file contents>, truncated: bool, original_size: int }
      - { type: 'error', error: <error message> }
    """
    try:
        # Try structured parsing
        entries = extract_file_overview(path)
        headers = [e['header'] for e in entries if e.get('header')]
        if headers:
            return {'type': 'overview', 'entries': headers}
        # If parser ran but yielded nothing useful, fall back to raw
    except ValueError as e:
        # Unsupported extension - this is expected for many files
        pass
    except Exception as e:
        # Unexpected parsing error
        return {'type': 'error', 'error': f"Parser error: {e}"}

    # Fallback: raw content with truncation
    def _read_and_truncate(file_path: str, encoding: str, errors: str = 'strict') -> dict:
        with open(file_path, 'r', encoding=encoding, errors=errors) as f:
            text = f.read()
        
        original_size = len(text)
        truncated = original_size > max_chars
        
        if truncated:
            text = text[:max_chars]
        
        return {
            'type': 'raw', 
            'text': text, 
            'truncated': truncated,
            'original_size': original_size
        }
    
    try:
        # Try UTF-8 first
        return _read_and_truncate(path, 'utf-8')
    except UnicodeDecodeError:
        # Try with error handling for binary/mixed content
        try:
            return _read_and_truncate(path, 'utf-8', errors='ignore')
        except Exception as e:
            return {'type': 'error', 'error': f"Could not read file: {e}"}
    except Exception as e:
        return {'type': 'error', 'error': f"Could not read file: {e}"}

def _read_raw_content_with_truncation(file_path: str, max_chars: int) -> dict:
    """Helper function to read and truncate file content consistently"""
    def _read_and_truncate(file_path: str, encoding: str, errors: str = 'strict') -> dict:
        with open(file_path, 'r', encoding=encoding, errors=errors) as f:
            text = f.read()
        
        original_size = len(text)
        truncated = original_size > max_chars
        
        if truncated:
            text = text[:max_chars]
        
        return {
            'type': 'raw', 
            'text': text, 
            'truncated': truncated,
            'original_size': original_size
        }
    
    try:
        # Try UTF-8 first
        return _read_and_truncate(file_path, 'utf-8')
    except UnicodeDecodeError:
        # Try with error handling for binary/mixed content
        try:
            return _read_and_truncate(file_path, 'utf-8', errors='ignore')
        except Exception as e:
            return {'type': 'error', 'error': f"Error reading file: {e}"}
    except Exception as e:
        return {'type': 'error', 'error': f"Error reading file: {e}"}

def resolve_needed_info(entry: UnsureEntry, root_path: str) -> dict:
    """
    Resolve the needed information for an unsure entry.
    Returns appropriate data structure based on entry.needed_info.
    """
    # Use safe path joining to avoid mixed separators
    full_path = safe_join_path(root_path, entry.path)
    
    # Check if it's a directory first, regardless of needed_info
    if os.path.isdir(full_path):
        return {
            'type': 'directory',
            'message': f"Path {entry.path} is a directory that should be analyzed separately"
        }

    if entry.needed_info in ('file_overview', 'file_metadata'):
        return safe_file_overview(full_path)

    if entry.needed_info == 'raw_content':
        return _read_raw_content_with_truncation(full_path, max_chars=10000)

    return {'type': 'error', 'error': f"Unknown needed_info: {entry.needed_info}"}

async def resolve_needed_info_async(entry: UnsureEntry, root_path: str, executor: ThreadPoolExecutor) -> dict:
    """Async version of resolve_needed_info for parallel I/O operations"""
    loop = asyncio.get_event_loop()
    
    def resolve_sync():
        return resolve_needed_info(entry, root_path)
    
    return await loop.run_in_executor(executor, resolve_sync)
