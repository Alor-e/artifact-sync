(function () {
  const vscode = acquireVsCodeApi();

  const summaryEl = document.getElementById('summary');
  const impactedListEl = document.getElementById('impacted-list');
  const impactedCountEl = document.getElementById('impacted-count');
  const logsEl = document.getElementById('log-stream');
  const runBtn = document.getElementById('run-again');
  const copyLogsBtn = document.getElementById('copy-logs');
  const settingsPanel = document.getElementById('settings');
  const settingsBody = document.getElementById('settings-body');
  const toggleSettingsBtn = document.getElementById('toggle-settings');
  const providerSelect = document.getElementById('setting-provider');
  const modelInput = document.getElementById('setting-model');
  const depthInput = document.getElementById('setting-max-depth');
  const retriesInput = document.getElementById('setting-max-retries');

  function applyThemeClass() {
    const bodyClass = document.body.classList;
    const isLight = bodyClass.contains('vscode-light');
    const isHighContrast = bodyClass.contains('vscode-high-contrast');
    bodyClass.remove('theme-dark', 'theme-light', 'theme-high-contrast');
    if (isHighContrast) {
      bodyClass.add('theme-high-contrast');
    } else if (isLight) {
      bodyClass.add('theme-light');
    } else {
      bodyClass.add('theme-dark');
    }
  }

  applyThemeClass();
  new MutationObserver(() => applyThemeClass()).observe(document.body, { attributes: true, attributeFilter: ['class'] });

  const state = {
    logs: [],
    analysis: null,
    running: false,
    pendingFixes: new Set(),
    settings: {
      provider: 'OPENAI',
      model: 'gpt-5-mini',
      maxDepth: 3,
      maxRetries: 3,
    },
    collapsedCards: new Set(),
  };

  runBtn?.addEventListener('click', () => {
    if (state.running) {
      return;
    }
    const settings = collectSettings();
    vscode.postMessage({ type: 'requestAnalysis', payload: settings });
  });

  toggleSettingsBtn?.addEventListener('click', () => {
    if (!settingsPanel) {
      return;
    }
    const collapsed = settingsPanel.classList.toggle('collapsed');
    toggleSettingsBtn.textContent = collapsed ? 'Show' : 'Hide';
  });

  providerSelect?.addEventListener('change', () => {
    state.settings.provider = String(providerSelect.value || 'OPENAI').toUpperCase();
    syncSettingsInputs();
  });

  modelInput?.addEventListener('change', () => {
    const value = modelInput.value.trim();
    if (value) {
      state.settings.model = value;
    }
    syncSettingsInputs();
  });

  depthInput?.addEventListener('change', () => {
    state.settings.maxDepth = sanitizeNumber(depthInput.value, state.settings.maxDepth);
    syncSettingsInputs();
  });

  retriesInput?.addEventListener('change', () => {
    state.settings.maxRetries = sanitizeNumber(retriesInput.value, state.settings.maxRetries);
    syncSettingsInputs();
  });

  function sanitizeNumber(value, fallback) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      return fallback;
    }
    return Math.floor(parsed);
  }

  function collectSettings() {
    const provider = String(providerSelect?.value || state.settings.provider || 'OPENAI').toUpperCase();
    const modelValue = modelInput?.value?.trim();
    const model = modelValue && modelValue.length ? modelValue : state.settings.model;
    const maxDepth = sanitizeNumber(depthInput?.value, state.settings.maxDepth);
    const maxRetries = sanitizeNumber(retriesInput?.value, state.settings.maxRetries);

    state.settings = { provider, model, maxDepth, maxRetries };
    syncSettingsInputs();
    return { provider, model, maxDepth, maxRetries };
  }

  function wrapSummary(content, variant = 'default') {
    const variantClass = variant === 'empty' ? ' summary-surface--empty' : '';
    return `<div class="summary-surface${variantClass}">${content}</div>`;
  }

  function applySettingsSnapshot(snapshot = {}) {
    if (snapshot.provider) {
      state.settings.provider = String(snapshot.provider).toUpperCase();
    }
    if (snapshot.model) {
      state.settings.model = snapshot.model;
    }
    if (typeof snapshot.maxDepth === 'number') {
      state.settings.maxDepth = sanitizeNumber(snapshot.maxDepth, state.settings.maxDepth);
    }
    if (typeof snapshot.maxRetries === 'number') {
      state.settings.maxRetries = sanitizeNumber(snapshot.maxRetries, state.settings.maxRetries);
    }
    syncSettingsInputs();
  }

  function syncSettingsInputs() {
    if (providerSelect) {
      providerSelect.value = state.settings.provider;
    }
    if (modelInput) {
      modelInput.value = state.settings.model;
    }
    if (depthInput) {
      depthInput.value = String(state.settings.maxDepth);
    }
    if (retriesInput) {
      retriesInput.value = String(state.settings.maxRetries);
    }
  }

  copyLogsBtn?.addEventListener('click', () => {
    if (!state.logs.length) {
      return;
    }
    const payload = state.logs
      .map((entry) => `[${entry.timestamp}] [${entry.level.toUpperCase()}] ${entry.message}`)
      .join('\n');
    vscode.postMessage({ type: 'copyLogs', payload });
  });

  window.addEventListener('message', (event) => {
    const message = event.data;
    if (!message) {
      return;
    }

    switch (message.type) {
      case 'analysisStart':
        handleAnalysisStart(message.payload);
        break;
      case 'analysisLog':
        handleAnalysisLog(message.payload);
        break;
      case 'analysisResult':
        handleAnalysisResult(message.payload);
        break;
      case 'analysisError':
        handleAnalysisError(message.payload);
        break;
      case 'configSnapshot':
        applySettingsSnapshot(message.payload || {});
        break;
      case 'fixApplied':
        updateFixButton(message.payload?.path, 'complete');
        break;
      case 'fixFailed':
        updateFixButton(message.payload?.path, 'idle');
        break;
      default:
        break;
    }
  });

  function handleAnalysisStart(payload) {
    state.running = true;
    state.analysis = null;
    state.logs = [];
    state.pendingFixes.clear();
    state.collapsedCards.clear();
    impactedCountEl.textContent = '0';
    runBtn.disabled = true;
    runBtn.textContent = 'Running…';

    if (payload?.provider) {
      state.settings.provider = String(payload.provider).toUpperCase();
    }
    if (payload?.model) {
      state.settings.model = payload.model;
    }
    syncSettingsInputs();

    summaryEl.innerHTML = wrapSummary(renderRunningState(payload));
    impactedListEl.innerHTML = '<div class="empty-state">Collecting impact data…</div>';
    const startLog = {
      timestamp: new Date().toISOString(),
      level: 'info',
      message: `Starting analysis on ${payload?.rootPath ?? 'workspace'} using ${payload?.provider ?? 'provider'} (${payload?.model ?? 'model'}).`,
    };
    state.logs.push(startLog);
    logsEl.innerHTML = renderLogs(state.logs);
    logsEl.scrollTop = logsEl.scrollHeight;
    state.logs.push(startLog);
    logsEl.innerHTML = renderLogs(state.logs);
  }

  function handleAnalysisLog(entry) {
    if (!entry || !entry.message) {
      return;
    }
    state.logs.push(entry);
    logsEl.innerHTML = renderLogs(state.logs);
    logsEl.scrollTop = logsEl.scrollHeight;
  }

  function handleAnalysisResult(data) {
    state.running = false;
    state.analysis = data;
    runBtn.disabled = false;
    runBtn.textContent = 'Run Artifact Sync';

    renderAnalysis(data);
  }

  function handleAnalysisError(message) {
    state.running = false;
    state.analysis = null;
    runBtn.disabled = false;
    runBtn.textContent = 'Run Artifact Sync';

    summaryEl.innerHTML = wrapSummary(renderErrorState(message || 'Analysis failed'), 'empty');
    impactedListEl.innerHTML = '';
    impactedCountEl.textContent = '0';

    if (message) {
      state.logs.push({
        timestamp: new Date().toISOString(),
        level: 'error',
        message,
      });
      logsEl.innerHTML = renderLogs(state.logs);
    }
  }

  function renderAnalysis(data) {
    if (!data || !data.metadata) {
      summaryEl.innerHTML = wrapSummary(renderErrorState('Unexpected analysis payload'), 'empty');
      impactedListEl.innerHTML = '';
      impactedCountEl.textContent = '0';
      return;
    }

    summaryEl.innerHTML = wrapSummary(renderSummary(data.metadata, data.metrics, data.stillUnsure));

    impactedCountEl.textContent = String(data.impacted?.length || 0);
    impactedListEl.innerHTML = renderImpactedList(data.impacted || []);

    if (!state.logs.length) {
      logsEl.innerHTML = '<div class="empty-state">Logs will appear here once the agent starts.</div>';
    } else {
      logsEl.innerHTML = renderLogs(state.logs);
    }

    attachFixHandlers();
  }

  function renderRunningState(payload) {
    const provider = escapeHtml(payload?.provider ?? '');
    const model = escapeHtml(payload?.model ?? '');
    const rootPath = escapeHtml(payload?.rootPath ?? '');
    return `
      <div class="summary-grid">
        <div class="metric-card">
          <span class="metric-label">Status</span>
          <span class="metric-value">Analyzing…</span>
          <span class="metric-subtle">${rootPath || 'Preparing workspace context'}</span>
          <span class="metric-subtle">Provider ${provider} · ${model}</span>
        </div>
      </div>
    `;
  }

  function renderSummary(metadata, metrics, unsure) {
    const runStart = metadata.runStartedAt ? new Date(metadata.runStartedAt) : new Date();
    const elapsed = typeof metadata.elapsedSeconds === 'number' ? metadata.elapsedSeconds : 0;

    const totalTokens = metrics?.totalTokens ?? metrics?.promptTokens + metrics?.completionTokens ?? 0;
    const unsureSection = unsure && unsure.length
      ? `
        <div class="unsure">
          <strong>Needs follow-up (${unsure.length})</strong>
          <div>${unsure
            .map((item) => `${escapeHtml(item.path)} — ${escapeHtml(item.reason)}`)
            .join('<br/>')}</div>
        </div>
      `
      : '';

    return `
      <div class="summary-grid">
        <div class="metric-card">
          <span class="metric-label">Provider</span>
          <span class="metric-value">${escapeHtml(metadata.provider)}</span>
          <span class="metric-subtle">${escapeHtml(metadata.model)}</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">Repository</span>
          <span class="metric-value small" title="${escapeHtml(metadata.rootPath)}">${escapeHtml(shortenPath(metadata.rootPath))}</span>
          <span class="metric-subtle">Workspace target</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">Run started</span>
          <span class="metric-value">${runStart.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          <span class="metric-subtle">${formatElapsed(elapsed)}</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">Tokens</span>
          <span class="metric-value">${(totalTokens || 0).toLocaleString()}</span>
          <span class="metric-subtle">Prompt ${(metrics?.promptTokens ?? 0).toLocaleString()} · Completion ${(metrics?.completionTokens ?? 0).toLocaleString()}</span>
        </div>
      </div>
      ${unsureSection}
    `;
  }

  function renderImpactedList(items) {
    if (!items.length) {
      return '<div class="empty-state">No impacted files detected.</div>';
    }

    const paths = new Set(items.map((item) => item.path));
    for (const pathKey of Array.from(state.collapsedCards)) {
      if (!paths.has(pathKey)) {
        state.collapsedCards.delete(pathKey);
      }
    }

    return items
      .map((item) => {
        const recommendations = (item.recommendations || [])
          .map((rec) => `<li>${escapeHtml(rec)}</li>`)
          .join('');
        const needsUpdate = item.needsUpdate ? '<span class="chip update">Needs Update</span>' : '';
        const collapsed = state.collapsedCards.has(item.path);
        const disabled = state.pendingFixes.has(item.path) ? 'disabled' : '';
        const label = state.pendingFixes.has(item.path) ? 'Applying…' : 'Generate Fix';
        const escapedPath = escapeHtml(item.path);

        return `
          <article class="file-card ${collapsed ? 'collapsed' : ''}" data-path="${escapedPath}">
            <header class="file-header">
              <div class="file-meta">
                <div class="path" title="${escapedPath}">${escapedPath}</div>
                <div class="chips">
                  <span class="chip ${item.confidence}">${item.confidence} confidence</span>
                  <span class="chip ${item.impact}">${item.impact} impact</span>
                  ${needsUpdate}
                </div>
              </div>
              <div class="file-actions">
                <button class="icon-button" data-action="toggle" data-path="${escapedPath}" aria-expanded="${String(!collapsed)}" aria-label="${collapsed ? 'Expand details' : 'Collapse details'}"></button>
                <button class="secondary" data-action="fix" data-path="${escapedPath}" ${disabled}>${label}</button>
              </div>
            </header>
            <div class="file-body">
              <p class="summary-text">${escapeHtml(item.summary)}</p>
              <div class="recommendations">
                <strong>Recommended Actions</strong>
                <ul class="recommendations-list">
                  ${recommendations}
                </ul>
              </div>
            </div>
          </article>
        `;
      })
      .join('');
  }

  function renderLogs(logs = []) {
    if (!logs.length) {
      return '<div class="empty-state">Logs will appear here once the agent starts.</div>';
    }

    return logs
      .map(
        (entry) => `
        <div class="log-entry">
          <span class="timestamp">${new Date(entry.timestamp).toLocaleTimeString()}</span>
          <span class="level ${entry.level}">${entry.level}</span>
          <span>${escapeHtml(entry.message)}</span>
        </div>
      `,
      )
      .join('');
  }

  function updateFixButton(targetPath, stateName) {
    if (!targetPath) {
      return;
    }
    const escapedTarget = typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
      ? CSS.escape(targetPath)
      : targetPath.replace(/"/g, '\"');
    const card = document.querySelector(`.file-card[data-path="${escapedTarget}"]`);
    const button = card?.querySelector('[data-action="fix"]');
    const toggleButton = card?.querySelector('[data-action="toggle"]');
    if (!card || !button) {
      return;
    }

    if (stateName === 'complete') {
      state.pendingFixes.delete(targetPath);
      button.textContent = 'Applied';
      button.classList.add('success');
      button.disabled = true;
    } else if (stateName === 'pending') {
      state.pendingFixes.add(targetPath);
      button.textContent = 'Applying…';
      button.classList.remove('success');
      button.disabled = true;
    } else {
      state.pendingFixes.delete(targetPath);
      button.textContent = 'Generate Fix';
      button.classList.remove('success');
      button.disabled = false;
    }

    if (toggleButton) {
      toggleButton.disabled = state.pendingFixes.has(targetPath) && stateName === 'pending';
    }
  }

  function attachFixHandlers() {
    const cards = document.querySelectorAll('.file-card');
    cards.forEach((card) => {
      const path = card.getAttribute('data-path');
      if (!path) {
        return;
      }

      const fixButton = card.querySelector('[data-action="fix"]');
      if (fixButton && fixButton.getAttribute('data-bound') !== 'true') {
        fixButton.setAttribute('data-bound', 'true');
        fixButton.addEventListener('click', (event) => {
          event.stopPropagation();
          if (state.pendingFixes.has(path)) {
            return;
          }
          updateFixButton(path, 'pending');
          vscode.postMessage({ type: 'applyFix', payload: { path } });
        });
      }

      const toggleButton = card.querySelector('[data-action="toggle"]');
      const header = card.querySelector('.file-header');
      const toggleHandler = () => {
        const collapsed = !card.classList.contains('collapsed');
        card.classList.toggle('collapsed', collapsed);
        if (toggleButton) {
          toggleButton.setAttribute('aria-expanded', String(!collapsed));
          toggleButton.setAttribute('aria-label', collapsed ? 'Expand details' : 'Collapse details');
        }
        if (collapsed) {
          state.collapsedCards.add(path);
        } else {
          state.collapsedCards.delete(path);
        }
      };

      if (toggleButton && toggleButton.getAttribute('data-bound') !== 'true') {
        toggleButton.setAttribute('data-bound', 'true');
        toggleButton.addEventListener('click', (event) => {
          event.stopPropagation();
          toggleHandler();
        });
      }

      if (header && header.getAttribute('data-bound') !== 'true') {
        header.setAttribute('data-bound', 'true');
        header.addEventListener('click', (event) => {
          // allow fix button clicks to pass through
          if ((event.target instanceof HTMLElement ? event.target : null)?.closest('[data-action="fix"]')) {
            return;
          }
          toggleHandler();
        });
      }
    });
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function shortenPath(pathValue) {
    const path = String(pathValue ?? '');
    if (path.length <= 32) {
      return path;
    }
    const parts = path.split(/\\|\//);
    if (parts.length <= 2) {
      return `…/${parts.pop()}`;
    }
    return `${parts[0]}/…/${parts[parts.length - 1]}`;
  }

  function formatElapsed(seconds) {
    if (!seconds || Number.isNaN(seconds)) {
      return 'Duration unavailable';
    }
    if (seconds < 60) {
      return `${seconds.toFixed(1)}s elapsed`;
    }
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.round(seconds % 60);
    return `${minutes}m ${remainder}s elapsed`;
  }

  function renderErrorState(message) {
    return `
      <div class="empty-state error">${escapeHtml(message)}</div>
    `;
  }

  syncSettingsInputs();

  // Ensure initial empty state
  summaryEl.innerHTML = wrapSummary('<div class="empty-state">Run Artifact Sync to explore change-impact insights.</div>', 'empty');
  impactedListEl.innerHTML = '<div class="empty-state">No analysis run yet.</div>';
  logsEl.innerHTML = '<div class="empty-state">Logs will appear here once the agent starts.</div>';

  // Re-attach handlers after rendering impacted list
  const observer = new MutationObserver(() => {
    attachFixHandlers();
  });
  observer.observe(impactedListEl, { childList: true });
})();
