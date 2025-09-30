import { cpSync, rmSync, existsSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const extensionRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(extensionRoot, '..');
const source = path.resolve(repoRoot, 'change-impact-agent');
const destination = path.resolve(extensionRoot, 'python');

if (!existsSync(source)) {
  console.error('change-impact-agent directory not found at', source);
  process.exit(1);
}

rmSync(destination, { recursive: true, force: true });

const excluded = new Set(['__pycache__', '.venv', '.git']);

cpSync(source, destination, {
  recursive: true,
  filter: (src) => {
    const basename = path.basename(src);
    if (excluded.has(basename)) {
      return false;
    }
    // skip cache files
    if (basename.endsWith('.pyc')) {
      return false;
    }
    return true;
  },
});

console.log('Synced change-impact-agent ->', destination);
