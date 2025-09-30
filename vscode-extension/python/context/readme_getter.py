import argparse
from pathlib import Path
from typing import Optional

README_NAMES = (
    "README.md",
    "README.rst",
    "README.txt",
    "README"
)

def get_readme_content(repo_root: str, max_chars: int = 10_000) -> Optional[str]:
    """
    Look for a README file in the repo root and return its text (trimmed to max_chars).
    Returns None if no README is found.
    """
    root = Path(repo_root)
    for name in README_NAMES:
        readme_path = root / name
        if readme_path.is_file():
            try:
                text = readme_path.read_text(encoding="utf-8", errors="ignore")
                # If too large, truncate to avoid overwhelming the LLM
                return text if len(text) <= max_chars else text[:max_chars] + "\n…(truncated)…"
            except Exception as e:
                print(f"[README] Failed to read {readme_path}: {e}")
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Print any readme files from repo"
    )
    parser.add_argument(
        "-d", "--repo-dir",
        required=True,
        help="Path to the root of the Git repository"
    )
    args = parser.parse_args()

    readme_content = get_readme_content(args.repo_dir)
    print(readme_content)