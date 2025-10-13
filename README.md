# Artifact Sync

Artifact Sync pairs a packaged VS Code extension with a Python analysis agent to surface change-impact insights directly inside the editor. The tool inspects recent repository changes, highlights impacted files, and (optionally) generates remediation guidance or fixes.

The Python agent powering Artifact Sync is also published as a standalone project: [artifact-sync-backend](https://github.com/Alor-e/artifact-sync-backend).

---

## Quick Start for Reviewers

1. **Download the extension** – Grab `ArtifactSyncAssistant.vsix` from the repository root or [download it directly](ArtifactSyncAssistant.vsix).
2. **Install in VS Code** – Launch VS Code (v1.84+), open the Extensions view, choose “Install from VSIX…”, and select the file.
3. **Open a repository** – Load the project you want to analyze in VS Code. Ensure Python 3.10+ is available on your machine.
4. **Set an API key** – Run the command palette (`Ctrl/Cmd` + `Shift` + `P`) → `Artifact Sync: Set API Key`, then paste an OpenAI, Anthropic, or Google Gemini token. Secrets are stored securely via VS Code’s secret storage.
5. **Run the dashboard** – Execute `Artifact Sync: Open Dashboard` from the command palette and press **Run Artifact Sync** to launch an analysis. Logs stream live in the Activity Log panel.

> 💡 A short walkthrough video is available here: [Demo Video](https://example.com/demo)

---

## What You’ll See

- **Header controls** for running analyses and toggling advanced settings.
- **Summary metrics**: provider, repository path, start time, token usage, and “needs follow-up” notes.
- **Impacted Files list** with confidence/impact badges, recommended actions, and one-click fix generation.
- **Activity Log** streaming the agent’s progress (context building, refinement iterations, fix application, etc.).

Results live inside the dashboard session. Close and reopen the panel whenever you want a fresh run; API keys persist between sessions.

---

## Feature Highlights

- **Unified LLM orchestration** – Seamlessly targets OpenAI, Google Gemini, or Anthropic models through a single provider layer.
- **Repository-context prompts** – Supplies directory trees, diff summaries, and README excerpts so the agent understands project structure.
- **Parallel refinement** – Handles uncertain files in batches, escalating from structural overviews to raw content only when needed.
- **Optional fix generation** – Produces whole-file patches for artifacts flagged as “Needs Update,” saving them for manual review.
- **Secure credentials** – API keys are stored in VS Code’s encrypted secret storage; the repository never contains keys in plain text.

---

## Repository Layout

```text
artifact-sync/
├── ArtifactSyncAssistant.vsix          # Ready-to-install extension package (root copy)
├── change-impact-agent/                # Python backend (mirrors the standalone CLI project)
│   ├── analysis/                       # Refinement loop, reporting, and fix generation
│   ├── context/                        # Tree, diff, and README builders
│   ├── llm/                            # Provider adapters + unified chat manager
│   └── ...
├── vscode-extension/                   # VS Code extension sources
│   ├── artifact-sync-assistant-0.0.1.vsix
│                                       # Packaged extension emitted by npm run package
│   ├── media/                          # Dashboard HTML/CSS/JS assets
│   ├── out/                            # Compiled TypeScript output
│   ├── python/                         # Bundled backend copied during packaging
│   └── src/                            # Extension + webview controller logic
└── README.md                           # This document
```

The Python backend retains its own detailed README (`change-impact-agent/README.md`) for CLI usage; no reviewer-only instructions are necessary there.

---

## Behind the Scenes

- When the dashboard runs, the extension provisions a virtual environment in VS Code’s global storage and installs dependencies from `change-impact-agent/requirements.txt` automatically.
- `PythonBridge` handles spawning `main.py`, streaming logs, applying generated fixes via `WorkspaceEdit`, and caching settings between runs.
- Theme-aware CSS/JS assets inside `media/` keep the UI aligned with VS Code light/dark/high-contrast themes.

---

## Model Guidance

- Internal experiments (and the accompanying paper demo) relied on the `gpt-5-mini` model.
- Lighter Gemini releases—especially `gemini-flash-lite-latest`—complete significantly faster while still producing actionable insights.

---

## Maintainer Notes

If you need to regenerate the VSIX before sharing the repository:

```bash
cd vscode-extension
npm install --save-dev @vscode/vsce   # only once
npm run package
cp artifact-sync-assistant-0.0.1.vsix ../ArtifactSyncAssistant.vsix
```

`npm run package` automatically syncs `change-impact-agent/` into `vscode-extension/python/` so the VSIX always bundles the backend.

Update the version number in `package.json` (and the copy commands) if you publish a new release.

---

For questions, issues, or contributions, please open an issue on the project GitHub page: [Alor-e/artifact-sync](https://github.com/Alor-e/artifact-sync).
- **Model guidance** – Internal experiments (and the accompanying paper demo) used the `gpt-5-mini` model. Lighter Gemini tiers such as `gemini-flash-lite-latest` run noticeably faster for exploratory testing; feel free to pick the trade-off that matches your latency and quality targets.
