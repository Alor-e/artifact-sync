import os
import fnmatch
from pathlib import Path
from typing import List, Dict, Optional, Union
from dataclasses import dataclass
import re

@dataclass
class GitIgnoreRule:
    """Represents a single .gitignore rule with its context"""
    pattern: str
    is_negation: bool
    is_directory_only: bool
    source_file: str
    line_number: int

class GitIgnoreProcessor:
    """
    Handles .gitignore file processing with support for hierarchical ignore rules.
    Supports standard .gitignore syntax including:
    - Basic patterns and wildcards
    - Directory-only patterns (ending with /)
    - Negation patterns (starting with !)
    - Hierarchical rules (parent .gitignore affects subdirectories)
    """
    
    def __init__(self, repo_root: Union[str, Path]):
        self.repo_root = Path(repo_root).resolve()
        self._ignore_cache: Dict[str, List[GitIgnoreRule]] = {}
        self._compiled_patterns: Dict[str, List] = {}
        
        # Build the ignore rule hierarchy
        self._build_ignore_hierarchy()
    
    def _build_ignore_hierarchy(self):
        """Build a hierarchy of .gitignore rules for the entire repository"""
        print(f"[GITIGNORE] Building ignore hierarchy for {self.repo_root}")
        
        # Walk through all directories to find .gitignore files
        for root, dirs, files in os.walk(self.repo_root, topdown=True):
            # Skip .git directory entirely
            if '.git' in dirs:
                dirs.remove('.git')

            if 'node_modules' in dirs:
                dirs.remove('node_modules')

            dirs[:] = [d for d in dirs if not (Path(root)/d).is_symlink()]
            
            root_path = Path(root)
            gitignore_path = root_path / '.gitignore'
            
            if gitignore_path.exists():
                relative_dir = str(root_path.relative_to(self.repo_root))
                if relative_dir == '.':
                    relative_dir = ''
                
                rules = self._parse_gitignore_file(gitignore_path)
                self._ignore_cache[relative_dir] = rules
                print(f"[GITIGNORE] Found {len(rules)} rules in {relative_dir or 'root'}/.gitignore")
    
    def _parse_gitignore_file(self, gitignore_path: Path) -> List[GitIgnoreRule]:
        """Parse a single .gitignore file and return list of rules"""
        rules = []
        
        try:
            with open(gitignore_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.rstrip('\n\r')
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Handle negation patterns
                    is_negation = line.startswith('!')
                    if is_negation:
                        line = line[1:]
                    
                    # Handle directory-only patterns
                    is_directory_only = line.endswith('/')
                    if is_directory_only:
                        line = line[:-1]
                    
                    # Skip empty patterns after processing
                    if not line:
                        continue
                    
                    rule = GitIgnoreRule(
                        pattern=line,
                        is_negation=is_negation,
                        is_directory_only=is_directory_only,
                        source_file=str(gitignore_path),
                        line_number=line_num
                    )
                    rules.append(rule)
        
        except Exception as e:
            print(f"[GITIGNORE] Warning: Could not parse {gitignore_path}: {e}")
        
        return rules
    
    def _get_applicable_rules(self, directory_path: str) -> List[GitIgnoreRule]:
        """Get all .gitignore rules that apply to a given directory path"""
        applicable_rules = []
        
        # Normalize the directory path
        if directory_path == '.':
            directory_path = ''
        
        # Get rules from current directory and all parent directories
        current_path = directory_path
        
        while True:
            if current_path in self._ignore_cache:
                # Add rules from this level (in reverse order so parent rules come first)
                applicable_rules = self._ignore_cache[current_path] + applicable_rules
            
            if not current_path:
                break
                
            # Move to parent directory
            if '/' in current_path:
                current_path = '/'.join(current_path.split('/')[:-1])
            else:
                current_path = ''
        
        return applicable_rules
    
    def _pattern_to_regex(self, pattern: str) -> str:
        """Convert a .gitignore pattern to a regex pattern"""
        # Escape special regex characters except for *, ?, [, ]
        pattern = re.escape(pattern)
        
        # Unescape the wildcards we want to handle specially
        pattern = pattern.replace(r'\*', '*').replace(r'\?', '?')
        pattern = pattern.replace(r'\[', '[').replace(r'\]', ']')
        
        # Handle ** (matches zero or more directories)
        pattern = pattern.replace('**', '.__DOUBLE_STAR__.')
        
        # Handle * (matches anything except /)
        pattern = pattern.replace('*', '[^/]*')
        
        # Handle ** replacement
        pattern = pattern.replace('.__DOUBLE_STAR__.', '.*')
        
        # Handle ? (matches any single character except /)
        pattern = pattern.replace('?', '[^/]')
        
        return pattern
    
    def _matches_pattern(self, file_path: str, rule: GitIgnoreRule, base_dir: str = '') -> bool:
        """Check if a file path matches a .gitignore rule"""
        pattern = rule.pattern
        
        # Handle leading slash (absolute pattern within repo)
        if pattern.startswith('/'):
            pattern = pattern[1:]
            # Absolute patterns only match from the directory containing the .gitignore
            if base_dir:
                full_pattern = f"{base_dir}/{pattern}"
            else:
                full_pattern = pattern
        else:
            # Relative patterns can match anywhere in the path
            full_pattern = pattern
        
        # Convert to regex
        regex_pattern = self._pattern_to_regex(full_pattern)
        
        try:
            # Try exact match first
            if re.fullmatch(regex_pattern, file_path):
                return True
            
            # For non-absolute patterns, try matching against path components
            if not rule.pattern.startswith('/'):
                path_parts = file_path.split('/')
                for i in range(len(path_parts)):
                    subpath = '/'.join(path_parts[i:])
                    if re.fullmatch(regex_pattern, subpath):
                        return True
                    
                    # Also try matching individual components for patterns without /
                    if '/' not in pattern:
                        if re.fullmatch(regex_pattern, path_parts[i]):
                            return True
            
            return False
            
        except re.error:
            # Fallback to simple fnmatch for problematic patterns
            return fnmatch.fnmatch(file_path, full_pattern)
    
    def should_ignore(self, file_path: str, is_directory: bool = False, base_dir: str = '') -> bool:
        """
        Determine if a file or directory should be ignored based on .gitignore rules.
        
        Args:
            file_path: Path relative to repository root
            is_directory: Whether the path is a directory
            base_dir: Base directory context for relative rules
        
        Returns:
            True if the file should be ignored, False otherwise
        """
        # Normalize path separators
        file_path = file_path.replace('\\', '/')
        base_dir = base_dir.replace('\\', '/') if base_dir else ''
        
        # Get applicable rules for the base directory
        applicable_rules = self._get_applicable_rules(base_dir)
        
        is_ignored = False
        
        # Process rules in order (parent rules first, then child rules)
        for rule in applicable_rules:
            # Skip directory-only rules for files
            if rule.is_directory_only and not is_directory:
                continue
            
            # Determine the base directory for this rule
            rule_base = str(Path(rule.source_file).parent.relative_to(self.repo_root))
            if rule_base == '.':
                rule_base = ''
            
            if self._matches_pattern(file_path, rule, rule_base):
                if rule.is_negation:
                    is_ignored = False  # Negation rules un-ignore files
                else:
                    is_ignored = True   # Normal rules ignore files
        
        return is_ignored
    
    def filter_paths(self, paths: List[str], base_dir: str = '') -> List[str]:
        """Filter a list of paths, removing those that should be ignored"""
        filtered = []
        
        for path in paths:
            # Determine if path is a directory
            full_path = self.repo_root / path
            is_directory = full_path.is_dir()
            
            if not self.should_ignore(path, is_directory, base_dir):
                filtered.append(path)
            else:
                print(f"[GITIGNORE] Ignoring: {path}")
        
        return filtered
    
    def get_ignore_stats(self) -> Dict[str, int]:
        """Get statistics about loaded .gitignore rules"""
        stats = {
            'total_gitignore_files': len(self._ignore_cache),
            'total_rules': sum(len(rules) for rules in self._ignore_cache.values()),
            'directories_with_gitignore': list(self._ignore_cache.keys())
        }
        return stats

def create_git_ignore_processor(repo_root: Union[str, Path]) -> Optional[GitIgnoreProcessor]:
    """
    Factory function to create a GitIgnoreProcessor if we're in a git repository.
    Returns None if not in a git repository.
    """
    repo_path = Path(repo_root).resolve()
    
    # Check if we're in a git repository
    git_dir = repo_path / '.git'
    if not git_dir.exists():
        # Try walking up to find .git directory
        current = repo_path
        while current != current.parent:
            if (current / '.git').exists():
                repo_path = current
                break
            current = current.parent
        else:
            print(f"[GITIGNORE] Warning: {repo_root} is not a git repository")
            return None
    
    try:
        processor = GitIgnoreProcessor(repo_path)
        stats = processor.get_ignore_stats()
        print(f"[GITIGNORE] Loaded {stats['total_rules']} rules from {stats['total_gitignore_files']} .gitignore files")
        return processor
    except Exception as e:
        print(f"[GITIGNORE] Error creating processor: {e}")
        return None