import * as vscode from 'vscode';
import * as path from 'path';
import { PythonBridge } from './pythonBridge';
import {
  AnalysisOptions,
  FixOptions,
  AgentAnalysisResponse,
  AgentFixResponse,
  DetailedImpactReportPayload,
  ModelProvider,
  BridgeLogEntry,
} from './types';

type WebviewImpactedItem = {
  path: string;
  confidence: 'low' | 'medium' | 'high';
  impact: 'direct' | 'indirect';
  summary: string;
  recommendations: string[];
  needsUpdate: boolean;
};

type WebviewAnalysisPayload = {
  metadata: {
    provider: string;
    model: string;
    rootPath: string;
    runStartedAt: string;
    elapsedSeconds: number;
  };
  metrics: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  };
  impacted: WebviewImpactedItem[];
  stillUnsure: Array<{ path: string; reason: string; neededInfo: string }>;
};

type AnalysisMessage =
  | { type: 'analysisStart'; payload: { provider: string; model: string; rootPath?: string } }
  | { type: 'analysisLog'; payload: BridgeLogEntry }
  | { type: 'analysisResult'; payload: WebviewAnalysisPayload }
  | { type: 'analysisError'; payload: string }
  | { type: 'configSnapshot'; payload: { provider: string; model: string; maxDepth: number; maxRetries: number } }
  | { type: 'fixApplied'; payload: { path: string } }
  | { type: 'fixFailed'; payload: { path: string; message: string } };

export function activate(context: vscode.ExtensionContext): void {
  const bridge = new PythonBridge(context);

  const runCommand = vscode.commands.registerCommand('artifactSync.openDashboard', async () => {
    if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
      void vscode.window.showErrorMessage('Open a workspace folder to run Artifact Sync.');
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      'artifactSyncDashboard',
      'Artifact Sync',
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: false },
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, 'media')],
      },
    );

    const session = new DashboardSession(context, panel, bridge);
    session.initialize();
  });

  const setKeyCommand = vscode.commands.registerCommand('artifactSync.setApiKey', async () => {
    await promptForApiKey(context);
  });

  context.subscriptions.push(runCommand, setKeyCommand);
}

export function deactivate(): void {
  // no-op
}

class DashboardSession {
  private readonly webview: vscode.Webview;
  private readonly bridge: PythonBridge;
  private readonly context: vscode.ExtensionContext;
  private currentRootPath: string | null = null;
  private currentProvider: ModelProvider = 'OPENAI';
  private currentModel: string = 'gpt-5-mini';
  private currentMaxDepth = 3;
  private currentMaxRetries = 3;
  private pendingAnalysis = false;
  private logs: BridgeLogEntry[] = [];

  constructor(context: vscode.ExtensionContext, panel: vscode.WebviewPanel, bridge: PythonBridge) {
    this.context = context;
    this.webview = panel.webview;
    this.bridge = bridge;

    panel.onDidDispose(() => {
      this.pendingAnalysis = false;
    });

    this.webview.onDidReceiveMessage((message) => {
      this.handleMessage(message).catch((error) => {
        void vscode.window.showErrorMessage(`Artifact Sync error: ${error instanceof Error ? error.message : String(error)}`);
      });
    });
  }

  initialize(): void {
    this.webview.html = getWebviewContent(this.webview, this.context.extensionUri);
    this.pushConfigSnapshot();
  }

  private pushConfigSnapshot(): void {
    const config = vscode.workspace.getConfiguration('artifactSync');
    const snapshot = {
      provider: (config.get<string>('provider') ?? 'OPENAI').toUpperCase(),
      model: config.get<string>('model') ?? 'gpt-5-mini',
      maxDepth: config.get<number>('maxDepth') ?? 3,
      maxRetries: config.get<number>('maxRetries') ?? 3,
    };
    this.postMessage({ type: 'configSnapshot', payload: snapshot });
    this.currentProvider = (snapshot.provider as ModelProvider) || 'OPENAI';
    this.currentModel = snapshot.model;
    this.currentMaxDepth = snapshot.maxDepth;
    this.currentMaxRetries = snapshot.maxRetries;
  }

  private async handleMessage(message: any): Promise<void> {
    switch (message?.type) {
      case 'requestAnalysis':
        await this.runAnalysis(message.payload);
        break;
      case 'copyLogs':
        if (typeof message.payload === 'string') {
          await vscode.env.clipboard.writeText(message.payload);
          void vscode.window.showInformationMessage('Artifact Sync logs copied to clipboard.');
        }
        break;
      case 'applyFix':
        if (message.payload?.path) {
          await this.generateFix(String(message.payload.path));
        }
        break;
      default:
        break;
    }
  }

  private async runAnalysis(overrides?: Partial<Pick<AnalysisOptions, 'provider' | 'model' | 'maxDepth' | 'maxRetries'>>): Promise<void> {
    if (this.pendingAnalysis) {
      return;
    }

    const rootPath = await this.pickWorkspaceFolder();
    if (!rootPath) {
      return;
    }

    const config = vscode.workspace.getConfiguration('artifactSync');
    const rawProvider = (overrides?.provider ?? config.get<string>('provider') ?? 'OPENAI').toUpperCase();
    const provider = (['OPENAI', 'GEMINI', 'ANTHROPIC'].includes(rawProvider)
      ? rawProvider
      : 'OPENAI') as ModelProvider;

    const overrideModel = overrides?.model?.trim();
    const model = overrideModel && overrideModel.length > 0
      ? overrideModel
      : config.get<string>('model') ?? 'gpt-5-mini';

    const normalizeNumber = (value: any, fallback: number) => {
      const parsed = Number(value);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        return fallback;
      }
      return Math.floor(parsed);
    };

    const maxDepth = normalizeNumber(overrides?.maxDepth, config.get<number>('maxDepth') ?? 3);
    const maxRetries = normalizeNumber(overrides?.maxRetries, config.get<number>('maxRetries') ?? 3);
    const pythonPathSetting = config.get<string>('pythonPath') || undefined;

    const apiKey = await getApiKey(this.context, provider);
    if (!apiKey) {
      void vscode.window.showWarningMessage(`Set an API key before running analysis (command: Artifact Sync: Set API Key).`);
      return;
    }

    const options: AnalysisOptions = {
      rootPath,
      provider,
      model,
      maxDepth,
      maxRetries,
      apiKey,
      pythonPathSetting,
    };

    this.currentRootPath = rootPath;
    this.currentProvider = provider;
    this.currentModel = model;
    this.currentMaxDepth = maxDepth;
    this.currentMaxRetries = maxRetries;
    this.logs = [];
    this.pendingAnalysis = true;

    this.postMessage({
      type: 'configSnapshot',
      payload: { provider, model, maxDepth, maxRetries },
    });
    this.postMessage({ type: 'analysisStart', payload: { provider, model, rootPath } });

    try {
      const result = await this.bridge.runAnalysis(options, {
        onLog: (entry) => {
          this.logs.push(entry);
          this.postMessage({ type: 'analysisLog', payload: entry });
        },
      });

      const payload = transformAnalysis(result);
      this.postMessage({ type: 'analysisResult', payload });
      this.postMessage({
        type: 'analysisLog',
        payload: {
          timestamp: new Date().toISOString(),
          level: 'info',
          message: `Analysis complete. ${payload.impacted.length} impacted item(s) identified.`,
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.postMessage({ type: 'analysisError', payload: message });
    } finally {
      this.pendingAnalysis = false;
    }
  }

  private async generateFix(targetPath: string): Promise<void> {
    if (!this.currentRootPath) {
      void vscode.window.showWarningMessage('Run the analysis before requesting fixes.');
      this.postMessage({ type: 'fixFailed', payload: { path: targetPath, message: 'Analysis not yet run.' } });
      return;
    }

    const config = vscode.workspace.getConfiguration('artifactSync');
    const model = this.currentModel || config.get<string>('model') || 'gpt-5-mini';
    const maxDepth = this.currentMaxDepth || config.get<number>('maxDepth') || 3;
    const maxRetries = this.currentMaxRetries || config.get<number>('maxRetries') || 3;
    const pythonPathSetting = config.get<string>('pythonPath') || undefined;

    const apiKey = await getApiKey(this.context, this.currentProvider);
    if (!apiKey) {
      void vscode.window.showWarningMessage(`Set an API key before generating fixes (command: Artifact Sync: Set API Key).`);
      this.postMessage({ type: 'fixFailed', payload: { path: targetPath, message: 'Missing API key.' } });
      return;
    }

    const options: FixOptions = {
      rootPath: this.currentRootPath,
      provider: this.currentProvider,
      model,
      maxDepth,
      maxRetries,
      apiKey,
      pythonPathSetting,
      targetPath,
    };

    try {
      const result = await this.bridge.runFix(options, {
        onLog: (entry) => {
          this.logs.push(entry);
          this.postMessage({ type: 'analysisLog', payload: entry });
        },
      });

      await this.applyFixToWorkspace(result);
      this.postMessage({ type: 'fixApplied', payload: { path: targetPath } });
      void vscode.window.showInformationMessage(`Applied fix for ${targetPath}.`);
      this.postMessage({
        type: 'analysisLog',
        payload: {
          timestamp: new Date().toISOString(),
          level: 'info',
          message: `Fix applied to ${targetPath}.`,
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.postMessage({ type: 'fixFailed', payload: { path: targetPath, message } });
      void vscode.window.showErrorMessage(`Generate fix failed: ${message}`);
    }
  }

  private async applyFixToWorkspace(result: AgentFixResponse): Promise<void> {
    const workspaceUri = vscode.Uri.file(path.join(this.currentRootPath!, result.path));

    const document = await vscode.workspace.openTextDocument(workspaceUri);
    const fullRange = new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length));
    const edit = new vscode.WorkspaceEdit();
    edit.replace(workspaceUri, fullRange, result.fixed_content);
    await vscode.workspace.applyEdit(edit);
    await document.save();
  }

  private postMessage(message: AnalysisMessage): void {
    void this.webview.postMessage(message);
  }

  private async pickWorkspaceFolder(): Promise<string | null> {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      return null;
    }
    if (folders.length === 1) {
      return folders[0].uri.fsPath;
    }

    const pick = await vscode.window.showQuickPick(
      folders.map((folder) => ({ label: folder.name, description: folder.uri.fsPath })),
      {
        placeHolder: 'Select the workspace folder to analyze',
      },
    );

    return pick?.description ?? null;
  }
}

function transformAnalysis(response: AgentAnalysisResponse): WebviewAnalysisPayload {
  const metadata = response.metadata ?? ({} as AgentAnalysisResponse['metadata']);
  const tokenUsage = response.token_usage ?? {};

  const impacted: WebviewImpactedItem[] = (response.report_entries || [])
    .filter((entry) => entry.parsed)
    .map((entry) => {
      const parsed = entry.parsed as DetailedImpactReportPayload;
      const impact = parsed.analysis?.impact === 'inderect' ? 'indirect' : parsed.analysis?.impact ?? 'indirect';
      const summary = parsed.analysis?.impact_description || parsed.diagnosis?.update_rationale || '';
      const recommendations = parsed.recommendations?.recommended_actions || [];
      return {
        path: entry.path,
        confidence: parsed.confidence,
        impact: impact as 'direct' | 'indirect',
        summary,
        recommendations,
        needsUpdate: parsed.diagnosis?.needs_update ?? false,
      };
    });

  const stillUnsure = (response.still_unsure || []).map((item) => ({
    path: item.path,
    reason: item.reason,
    neededInfo: item.needed_info,
  }));

  return {
    metadata: {
      provider: metadata.provider ?? '',
      model: metadata.model ?? '',
      rootPath: metadata.root_path ?? '',
      runStartedAt: metadata.run_started_at ?? new Date().toISOString(),
      elapsedSeconds: metadata.elapsed_seconds ?? 0,
    },
    metrics: {
      promptTokens: tokenUsage.prompt ?? 0,
      completionTokens: tokenUsage.completion ?? 0,
      totalTokens: tokenUsage.total ?? (tokenUsage.prompt ?? 0) + (tokenUsage.completion ?? 0),
    },
    impacted,
    stillUnsure,
  };
}

async function promptForApiKey(context: vscode.ExtensionContext): Promise<void> {
  const provider = (vscode.workspace.getConfiguration('artifactSync').get('provider') as ModelProvider) ?? 'OPENAI';
  const apiKey = await vscode.window.showInputBox({
    prompt: `Enter API key for ${provider}`,
    ignoreFocusOut: true,
    password: true,
  });
  if (apiKey) {
    await context.secrets.store(secretKeyName(provider), apiKey);
    void vscode.window.showInformationMessage(`${provider} API key stored securely.`);
  }
}

async function getApiKey(context: vscode.ExtensionContext, provider: ModelProvider): Promise<string | undefined> {
  return context.secrets.get(secretKeyName(provider));
}

function secretKeyName(provider: ModelProvider): string {
  return `artifactSync.apiKey.${provider}`;
}

function getWebviewContent(webview: vscode.Webview, extensionUri: vscode.Uri): string {
  const stylesheetUri = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, 'media', 'dashboard.css'));
  const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, 'media', 'dashboard.js'));
  const nonce = getNonce();

  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} https:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `font-src ${webview.cspSource} https:`,
    `script-src 'nonce-${nonce}'`,
  ].join('; ');

  return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta http-equiv="Content-Security-Policy" content="${csp}" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="stylesheet" href="${stylesheetUri}" />
    <title>Artifact Sync</title>
  </head>
  <body>
    <header class="app-header">
      <div class="brand">
        <div class="icon">⚙️</div>
        <div>
          <h1>Artifact Sync</h1>
          <p class="subtitle">Orchestrate change-impact insights and apply them confidently.</p>
        </div>
      </div>
      <button class="primary" id="run-again">Run Artifact Sync</button>
    </header>
    <main>
      <section id="summary" class="card glossy"></section>
      <section id="settings" class="card settings-card collapsed">
        <header>
          <div>
            <h2>Analysis Settings</h2>
            <p class="muted">Adjust provider, model, and traversal parameters before running.</p>
          </div>
          <button class="ghost" id="toggle-settings">Show</button>
        </header>
        <div class="settings-body" id="settings-body">
          <div class="settings-grid">
            <label class="input-field">
              <span>Provider</span>
              <select id="setting-provider">
                <option value="OPENAI">OpenAI</option>
                <option value="GEMINI">Google Gemini</option>
                <option value="ANTHROPIC">Anthropic</option>
              </select>
            </label>
            <label class="input-field">
              <span>Model</span>
              <input id="setting-model" type="text" placeholder="e.g. gpt-5-mini" />
            </label>
            <label class="input-field">
              <span>Max Depth</span>
              <input id="setting-max-depth" type="number" min="1" max="12" />
            </label>
            <label class="input-field">
              <span>Max Retries</span>
              <input id="setting-max-retries" type="number" min="1" max="10" />
            </label>
          </div>
        </div>
      </section>
      <section id="impacted" class="card">
        <header>
          <h2>Impacted Files</h2>
          <span class="badge" id="impacted-count">0</span>
        </header>
        <div id="impacted-list" class="list"></div>
      </section>
      <section id="logs" class="card">
        <header>
          <h2>Activity Log</h2>
          <button class="ghost" id="copy-logs">Copy</button>
        </header>
        <div id="log-stream" class="log-stream"></div>
      </section>
    </main>
    <script nonce="${nonce}" src="${scriptUri}"></script>
  </body>
</html>`;
}

function getNonce(): string {
  let text = '';
  const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i += 1) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}
