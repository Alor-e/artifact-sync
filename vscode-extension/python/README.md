# Change Impact Analysis Tool

An intelligent tool that analyzes Git repositories to identify which files are impacted by recent changes using AI.

## What it does

This tool combines repository structure analysis with AI-powered reasoning to:

- **Build a directory tree** to understand the repository structure 
- **Analyze commit diffs** to understand what changed in a repository
- **Use AI models** to intelligently determine which files are affected by changes
- **Perform multi-stage refinement** to reduce false positives and uncertain results

## Key Features

- **Smart Analysis**: Goes beyond simple file matching to identify downstream impacts (e.g., files that import changed modules)
- **Efficient Caching**: Caches AI responses and reuses chat contexts to minimize API costs
- **Subfolder Deep Dive**: Automatically analyzes folders at maximum depth to find impacted files within them
- **Iterative Refinement**: Uses multiple passes with different information levels (file overview -> raw content) to resolve uncertain cases
- **Multi-Provider AI Support**: Choose between Google, OpenAI, or Anthropic

## How it works

1. **Initial Analysis**: Scans the repository structure and recent diffs
2. **AI Classification**: Uses teh chosen AI model to classify files as "sure" (definitely impacted) or "unsure" (needs more analysis)
3. **Refinement Loops**: For uncertain files, progressively gathers more information and re-analyzes
4. **Subfolder Analysis**: Dives into relevant folders to find specific impacted files
5. **Final Results**: Outputs a list of impacted files

## Code Architecture

```python
root/
├── main.py                    # Entry point with configuration management
├── analysis/
│   ├── fixes.py               # Code generation
│   ├── refinement.py          # Multi-stage refinement
│   ├── reporting.py           # Results generation and reporting
│   └── subfolder.py           # Subfolder analysis logic
├── context/
│   ├── diff_getter.py         # Git diff extraction
│   ├── readme_getter.py       # Readme file extraction
│   └── tree_getter.py         # Repository structure building
├── core/
│   ├── abstractions.py        # Abstract base classes and interfaces
│   ├── config.py              # Configuration management
│   ├── errors.py              # Exception classes
│   └── schemas.py             # Pydantic models and data structures
├── llm/
│   ├── manager.py             # UnifiedChatManager
│   ├── providers/
│   │   ├── anthropic.py       # Anthropic implementation
│   │   ├── base.py            # Provider interface and factory
│   │   ├── gemini.py          # Google implementation
│   │   └── openai.py          # OpenAI implementation
├── parser/                    # File parsing and analysis
│   ├── dispatcher.py          # File overview extraction
│   ├── ... (language-specific parsers)
│   └── ... (structured data file parsers)
└── utils/
    ├── git_ignore_handler.py  # Process .gitignore files
    └── helpers.py             # Path handling and utilities
```
