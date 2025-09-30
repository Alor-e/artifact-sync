"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.PythonBridge = void 0;
const vscode = __importStar(require("vscode"));
const path = __importStar(require("path"));
const fs = __importStar(require("fs"));
const cp = __importStar(require("child_process"));
const util_1 = require("util");
const execFile = (0, util_1.promisify)(cp.execFile);
const PROVIDER_KEY_ENV = {
    OPENAI: 'OPENAI_API_KEY',
    GEMINI: 'GEMINI_API_KEY',
    ANTHROPIC: 'ANTHROPIC_API_KEY',
};
const PYTHON_DIR_CANDIDATES = ['python', '../change-impact-agent'];
function fileExists(target) {
    try {
        fs.accessSync(target, fs.constants.F_OK);
        return true;
    }
    catch {
        return false;
    }
}
function toBridgeLog(message) {
    const trimmed = message.trim();
    if (!trimmed) {
        return {
            timestamp: new Date().toISOString(),
            level: 'info',
            message: '',
        };
    }
    const levelMatch = trimmed.match(/^\[(INFO|WARN|ERROR)\]\s*(.*)$/i);
    if (levelMatch) {
        const [, rawLevel, rest] = levelMatch;
        const level = rawLevel.toLowerCase();
        return {
            timestamp: new Date().toISOString(),
            level,
            message: rest.trim(),
        };
    }
    return {
        timestamp: new Date().toISOString(),
        level: 'info',
        message: trimmed,
    };
}
async function ensureDirectory(uri) {
    try {
        await vscode.workspace.fs.createDirectory(uri);
    }
    catch (err) {
        // ignore if already exists
    }
}
function spawnProcess(command, args, options) {
    return new Promise((resolve, reject) => {
        const child = cp.spawn(command, args, options);
        let stderr = '';
        child.stderr?.on('data', (chunk) => {
            stderr += chunk.toString();
        });
        child.on('error', reject);
        child.on('close', (code) => {
            if (code === 0) {
                resolve();
            }
            else {
                reject(new Error(stderr || `Command failed: ${command} ${args.join(' ')}`));
            }
        });
    });
}
class PythonBridge {
    constructor(context) {
        this.context = context;
        this.pythonPrefixArgs = [];
        this.output = vscode.window.createOutputChannel('Artifact Sync Agent');
    }
    getAgentRoot() {
        for (const candidate of PYTHON_DIR_CANDIDATES) {
            const candidatePath = path.resolve(this.context.extensionPath, candidate);
            if (fileExists(candidatePath) && fs.statSync(candidatePath).isDirectory()) {
                return candidatePath;
            }
        }
        throw new Error('Unable to locate change-impact-agent sources packaged with the extension.');
    }
    async ensureEnvironment(pythonPathSetting) {
        if (!this.pythonExecutablePromise) {
            this.pythonExecutablePromise = this.createEnvironment(pythonPathSetting);
        }
        return this.pythonExecutablePromise;
    }
    async createEnvironment(pythonPathSetting) {
        const pythonPath = await this.resolveSystemPython(pythonPathSetting);
        const storageUri = this.context.globalStorageUri;
        await ensureDirectory(storageUri);
        const venvPath = path.join(storageUri.fsPath, 'ci-agent-venv');
        const venvPython = this.resolveVenvPython(venvPath);
        if (!fileExists(venvPython)) {
            this.output.appendLine(`Creating Python virtual environment at ${venvPath}`);
            await spawnProcess(pythonPath, [...this.pythonPrefixArgs, '-m', 'venv', venvPath], {
                cwd: this.getAgentRoot(),
            });
        }
        await this.installRequirements(venvPython);
        return venvPython;
    }
    resolveVenvPython(venvPath) {
        if (process.platform === 'win32') {
            return path.join(venvPath, 'Scripts', 'python.exe');
        }
        return path.join(venvPath, 'bin', 'python');
    }
    async installRequirements(pythonExecutable) {
        const agentRoot = this.getAgentRoot();
        const requirementsPath = path.join(agentRoot, 'requirements.txt');
        if (!fileExists(requirementsPath)) {
            this.output.appendLine('requirements.txt not found; skipping dependency installation.');
            return;
        }
        const lockPath = path.join(this.context.globalStorageUri.fsPath, 'requirements.lock');
        const requirementsStat = fs.statSync(requirementsPath);
        const requirementsStamp = String(requirementsStat.mtimeMs);
        let installedStamp;
        if (fileExists(lockPath)) {
            installedStamp = fs.readFileSync(lockPath, 'utf8');
        }
        if (installedStamp === requirementsStamp) {
            return;
        }
        this.output.appendLine('Installing Python dependencies for change-impact-agent…');
        await spawnProcess(pythonExecutable, [...this.pythonPrefixArgs, '-m', 'pip', '--disable-pip-version-check', 'install', '--upgrade', 'pip'], { cwd: agentRoot });
        await spawnProcess(pythonExecutable, [...this.pythonPrefixArgs, '-m', 'pip', '--disable-pip-version-check', 'install', '-r', requirementsPath], { cwd: agentRoot });
        fs.writeFileSync(lockPath, requirementsStamp, 'utf8');
    }
    async resolveSystemPython(pythonPathSetting) {
        const candidates = [];
        if (pythonPathSetting && pythonPathSetting.trim().length > 0) {
            candidates.push({ command: pythonPathSetting.trim(), versionArgs: ['--version'], prefix: [] });
        }
        if (process.platform === 'win32') {
            candidates.push({ command: 'python', versionArgs: ['--version'], prefix: [] });
            candidates.push({ command: 'py', versionArgs: ['-3', '--version'], prefix: ['-3'] });
        }
        else {
            candidates.push({ command: 'python3', versionArgs: ['--version'], prefix: [] });
            candidates.push({ command: 'python', versionArgs: ['--version'], prefix: [] });
        }
        for (const candidate of candidates) {
            try {
                const { stdout, stderr } = await execFile(candidate.command, candidate.versionArgs);
                const output = stdout || stderr;
                const match = output.match(/Python\s+(\d+)\.(\d+)\.(\d+)/i);
                if (!match) {
                    continue;
                }
                const major = Number(match[1]);
                const minor = Number(match[2]);
                if (major > 3 || (major === 3 && minor >= 10)) {
                    this.pythonPrefixArgs = candidate.prefix;
                    return candidate.command;
                }
            }
            catch (err) {
                // try next
            }
        }
        throw new Error('Failed to locate Python 3.10+ interpreter. Please specify path in settings.');
    }
    async runAnalysis(options, handlers = {}) {
        const pythonExecutable = await this.ensureEnvironment(options.pythonPathSetting);
        return this.runAgentProcess('analyze', pythonExecutable, options, handlers);
    }
    async runFix(options, handlers = {}) {
        const pythonExecutable = await this.ensureEnvironment(options.pythonPathSetting);
        return this.runAgentProcess('fix', pythonExecutable, options, handlers);
    }
    async runAgentProcess(action, pythonExecutable, options, handlers) {
        const agentRoot = this.getAgentRoot();
        const args = ['main.py', '--output-format', 'json'];
        args.push('--provider', options.provider);
        args.push('--model_name', options.model);
        args.push('--root_path', options.rootPath);
        args.push('--max_depth', String(options.maxDepth));
        args.push('--max_retries', String(options.maxRetries));
        if (action === 'fix') {
            args.push('--action', 'fix');
            args.push('--target-path', options.targetPath);
        }
        else {
            args.push('--action', 'analyze');
        }
        const env = {
            ...process.env,
            PYTHONUNBUFFERED: '1',
            MODEL_PROVIDER: options.provider,
        };
        const apiEnv = PROVIDER_KEY_ENV[options.provider];
        if (apiEnv) {
            env[apiEnv] = options.apiKey;
        }
        if (options.provider === 'OPENAI') {
            env.OPENAI_MODEL = options.model;
        }
        if (options.provider === 'GEMINI') {
            env.GEMINI_MODEL = options.model;
        }
        if (options.provider === 'ANTHROPIC') {
            env.ANTHROPIC_MODEL = options.model;
        }
        this.output.appendLine(`Spawning agent: ${pythonExecutable} ${args.join(' ')}`);
        return new Promise((resolve, reject) => {
            const spawnArgs = [...this.pythonPrefixArgs, ...args];
            const child = cp.spawn(pythonExecutable, spawnArgs, {
                cwd: agentRoot,
                env,
                shell: false,
            });
            let stdoutBuffer = '';
            let stderrBuffer = '';
            const flushLogBuffer = () => {
                let newlineIndex = stderrBuffer.indexOf('\n');
                while (newlineIndex >= 0) {
                    const line = stderrBuffer.slice(0, newlineIndex).trim();
                    stderrBuffer = stderrBuffer.slice(newlineIndex + 1);
                    if (line) {
                        const logEntry = toBridgeLog(line);
                        this.output.appendLine(`[${logEntry.level.toUpperCase()}] ${logEntry.message}`);
                        handlers.onLog?.(logEntry);
                    }
                    newlineIndex = stderrBuffer.indexOf('\n');
                }
            };
            child.stdout?.on('data', (chunk) => {
                stdoutBuffer += chunk.toString();
            });
            child.stderr?.on('data', (chunk) => {
                stderrBuffer += chunk.toString();
                flushLogBuffer();
            });
            child.on('error', (error) => {
                reject(error);
            });
            child.on('close', (code) => {
                if (stderrBuffer.trim().length > 0) {
                    const logEntry = toBridgeLog(stderrBuffer);
                    this.output.appendLine(`[${logEntry.level.toUpperCase()}] ${logEntry.message}`);
                    handlers.onLog?.(logEntry);
                    stderrBuffer = '';
                }
                if (code !== 0) {
                    reject(new Error(`Agent exited with code ${code}`));
                    return;
                }
                const trimmed = stdoutBuffer.trim();
                if (!trimmed) {
                    reject(new Error('Agent completed without emitting JSON payload.'));
                    return;
                }
                try {
                    const payload = JSON.parse(trimmed);
                    if (payload.status === 'error') {
                        reject(new Error(payload.message || 'Agent reported an error.'));
                    }
                    else {
                        resolve(payload.data);
                    }
                }
                catch (error) {
                    reject(new Error(`Failed to parse agent output: ${error.message}`));
                }
            });
        });
    }
}
exports.PythonBridge = PythonBridge;
//# sourceMappingURL=pythonBridge.js.map