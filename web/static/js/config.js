/**
 * Configuration Management JavaScript
 * Handles LLM, MCP, intents, and server configuration
 */

let currentEditServer = null;
let llmProviderInfo = {};
let confirmModalResolve = null;
let llmExtraConfig = {};
let mcpStatusCache = {};
let mcpServersCache = {};

async function initConfigPage() {
    if (window.__configInited) return;
    window.__configInited = true;
    await loadLLMProviders();
    loadConfig();
    setupTabs();
    loadIntents();
    loadMCPServers();
    loadPlugins();
    initMCPStatusStream();
    ilinkbot.init();
    loadCronTasks();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initConfigPage());
} else {
    initConfigPage();
}

function setupTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
        });
    });
}

function showConfirmDialog(title, message) {
    return new Promise(resolve => {
        confirmModalResolve = resolve;
        document.getElementById('confirmModalTitle').textContent = title;
        document.getElementById('confirmModalMessage').textContent = message;
        document.getElementById('confirmModal').style.display = 'flex';
    });
}

function closeConfirmModal(result) {
    document.getElementById('confirmModal').style.display = 'none';
    if (confirmModalResolve) {
        confirmModalResolve(result);
        confirmModalResolve = null;
    }
}

async function loadConfig() {
    const result = await apiCall('config.get');
    if (!result.success) return;

    const config = result.config;
    const llm = config.llm || {};
    const server = config.server || {};

    document.getElementById('llmProvider').value = llm.provider || '';
    document.getElementById('llmModel').value = llm.model || '';
    document.getElementById('llmEndpoint').value = llm.endpoint || '';
    document.getElementById('llmApiKey').value = llm.api_key || '';
    document.getElementById('llmVerbose').checked = llm.verbose !== false;
    document.getElementById('llmStream').checked = llm.stream !== false;
    document.getElementById('llmLogFile').value = llm.log_file || 'llm_engine.log';
    document.getElementById('llmMaxInputTokens').value = llm.max_input_tokens || 32768;
    document.getElementById('llmMaxGraphUpdates').value = llm.max_graph_updates || 5;
    llmExtraConfig = {
        planning_max_ask_rounds: llm.planning_max_ask_rounds || 5,
    };

    // Graph node sampling params (per phase)
    const graph = llm.graph || {};
    const planning = graph.planning || {};
    const execution = graph.execution || {};
    const finalizer = graph.finalizer || {};
    document.getElementById('graphPlanningTemp').value = numOr(planning.temperature, 0.2);
    document.getElementById('graphPlanningTopP').value = numOr(planning.top_p, 0.9);
    document.getElementById('graphExecutionTemp').value = numOr(execution.temperature, 0);
    document.getElementById('graphExecutionTopP').value = numOr(execution.top_p, 1);
    document.getElementById('graphFinalizerTemp').value = numOr(finalizer.temperature, 0.5);
    document.getElementById('graphFinalizerTopP').value = numOr(finalizer.top_p, 0.9);
    onProviderChange();

    // Server
    document.getElementById('servicePort').value = server.rpc_port || 11555;
    document.getElementById('adminPort').value = server.admin_port || 11556;
    document.getElementById('serverHost').value = server.host || '0.0.0.0';
    const langSelect = document.getElementById('languageSelect');
    if (langSelect) langSelect.value = server.language || 'zh-CN';

    const defaultLocation = config.default_location || {};
    document.getElementById('defaultLocationCity').value = defaultLocation.city || 'Nanjing';
    document.getElementById('nodeParallelCount').value = server.node_parallel_count || 1;
    document.getElementById('serverLogFile').value = server.log_file || 'debugout.log';
    document.getElementById('serverProxy').value = server.proxy || 'http://192.168.10.2:7890';
}

function onProviderChange() {
    const provider = document.getElementById('llmProvider').value;
    const info = llmProviderInfo[provider];
    document.getElementById('llmProviderDesc').textContent = info ? info.description : '';
}

async function loadLLMProviders() {
    const result = await apiCall('llm.providers');
    const sel = document.getElementById('llmProvider');
    if (!result.success || !result.providers) {
        sel.innerHTML = '<option value="ollama_native">Ollama</option>';
        return;
    }
    llmProviderInfo = {};
    for (const info of result.providers) {
        llmProviderInfo[info.provider] = info;
    }
    const current = sel.value;
    sel.innerHTML = '<option value="">-- 选择提供商 --</option>';
    for (const info of result.providers) {
        const opt = document.createElement('option');
        opt.value = info.provider;
        opt.textContent = `${info.description} (${info.provider})`;
        sel.appendChild(opt);
    }
    if (current && llmProviderInfo[current]) sel.value = current;
    onProviderChange();
}

// ============================================================
// LLM Config
// ============================================================

function numOr(value, fallback) {
    return (value === undefined || value === null || value === '') ? fallback : Number(value);
}

function getLLMConfig() {
    return {
        provider: document.getElementById('llmProvider').value,
        model: document.getElementById('llmModel').value,
        endpoint: document.getElementById('llmEndpoint').value,
        api_key: document.getElementById('llmApiKey').value,
        verbose: document.getElementById('llmVerbose').checked,
        stream: document.getElementById('llmStream').checked,
        log_file: document.getElementById('llmLogFile').value,
        max_input_tokens: parseInt(document.getElementById('llmMaxInputTokens').value) || 32768,
        max_graph_updates: parseInt(document.getElementById('llmMaxGraphUpdates').value) || 5,
        planning_max_ask_rounds: llmExtraConfig.planning_max_ask_rounds || 5,
        graph: {
            planning: {
                temperature: numOr(document.getElementById('graphPlanningTemp').value, 0.2),
                top_p: numOr(document.getElementById('graphPlanningTopP').value, 0.9),
            },
            execution: {
                temperature: numOr(document.getElementById('graphExecutionTemp').value, 0),
                top_p: numOr(document.getElementById('graphExecutionTopP').value, 1),
            },
            finalizer: {
                temperature: numOr(document.getElementById('graphFinalizerTemp').value, 0.5),
                top_p: numOr(document.getElementById('graphFinalizerTopP').value, 0.9),
            },
        },
    };
}

async function saveLLMConfig() {
    const result = await apiCall('config.update', {
        section: 'llm', values: getLLMConfig(),
    });
    if (result.success) showToast(__('config.llm.saveSuccess'), 'success');
    else showToast(__('config.llm.saveFailed') + (result.error || __('config.llm.unknownError')), 'error');
}

async function testLLM() {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = __('config.llm.testing');
    const resultEl = document.getElementById('llmTestResult');
    const result = await apiCall('llm.test');
    if (result.success) {
        resultEl.textContent = __('config.llm.testSuccess') + (result.response || '').substring(0, 100);
        resultEl.style.color = 'green';
    } else {
        resultEl.textContent = __('config.llm.testFailed') + (result.error || __('config.llm.unknownError'));
        resultEl.style.color = 'red';
    }
    btn.disabled = false;
    btn.textContent = __('config.llm.test');
}

function showSamplingHelp() {
    document.getElementById('samplingHelpModal').style.display = 'flex';
}

function closeSamplingHelp() {
    document.getElementById('samplingHelpModal').style.display = 'none';
}

// ============================================================
// MCP Config
// ============================================================

let registeredIntents = {};

async function loadIntents() {
    const result = await apiCall('intents.get');
    const tbody = document.getElementById('intentsTable');
    if (!result.success || !result.intents) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center">' + __('config.intents.loadFailed') + '</td></tr>';
        return;
    }
    registeredIntents = result.intents;
    const entries = Object.entries(registeredIntents);
    if (entries.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">' + __('config.intents.none') + '</td></tr>';
    } else {
        tbody.innerHTML = entries.map(([id, intent]) => {
            const isFixed = id === 'generic';
            const actions = isFixed
                ? `<span class="badge badge-secondary">${__('config.intents.fixed')}</span>`
                : `<button class="btn btn-sm btn-outline" onclick="openEditIntent('${id}')">${__('config.intents.editLabel')}</button>
                   <button class="btn btn-sm btn-outline" onclick="toggleIntent('${id}')">${intent.enabled ? __('config.intents.disable') : __('config.intents.enable')}</button>
                   <button class="btn btn-sm btn-danger" onclick="deleteIntent('${id}')">${__('config.intents.deleteLabel')}</button>`;
            return `
            <tr>
                <td><code>${id}</code></td>
                <td>${intent.name || id}</td>
                <td>${intent.description || '-'}</td>
                <td><span class="badge ${intent.enabled ? 'badge-success' : 'badge-danger'}">${intent.enabled ? __('config.intents.enabled') : __('config.intents.disabled')}</span></td>
                <td>${actions}</td>
            </tr>`;
        }).join('');
    }
    renderIntentCheckboxes();
}

function renderIntentCheckboxes() {
    const entries = Object.entries(registeredIntents);
    const containers = ['pluginDetailIntentCheckboxes'];
    containers.forEach(containerId => {
        const container = document.getElementById(containerId);
        if (!container) return;
        if (entries.length === 0) {
            container.innerHTML = '<span class="text-muted">' + __('common.noData') + '</span>';
            return;
        }
        container.innerHTML = entries.map(([id, intent]) => `
            <label>
                <input type="checkbox" class="intent-checkbox" data-container="${containerId}" value="${id}" onchange="updateMultiselectLabel('${containerId}')">
                ${intent.name || id}
            </label>
        `).join('');
        updateMultiselectLabel(containerId);
    });
}

function getSelectedIntents(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    return Array.from(container.querySelectorAll('.intent-checkbox:checked')).map(cb => cb.value);
}

function onBuiltinMCPChange() {}

// ── Multi-select Dropdown for Intent Categories ──────────

function toggleMultiselect(btn) {
    const menu = btn.nextElementSibling;
    const isOpen = menu.classList.contains('open');
    document.querySelectorAll('.multiselect-menu.open').forEach(m => {
        if (m !== menu) m.classList.remove('open');
    });
    document.querySelectorAll('.multiselect-toggle.open').forEach(b => {
        if (b !== btn) b.classList.remove('open');
    });
    menu.classList.toggle('open');
    btn.classList.toggle('open');
    if (!isOpen) {
        const closer = (e) => {
            if (!btn.closest('.multiselect-dropdown').contains(e.target)) {
                menu.classList.remove('open');
                btn.classList.remove('open');
                document.removeEventListener('click', closer);
            }
        };
        setTimeout(() => document.addEventListener('click', closer), 0);
    }
}

function updateMultiselectLabel(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const dropdown = container.closest('.multiselect-dropdown');
    if (!dropdown) return;
    const btn = dropdown.querySelector('.multiselect-toggle');
    if (!btn) return;
    const checked = container.querySelectorAll('.intent-checkbox:checked');
    const total = container.querySelectorAll('.intent-checkbox');
    if (checked.length === total.length) {
        const allLabel = btn.dataset.allLabel || 'config.mcp.allIntents';
        btn.textContent = __(allLabel) + ' (' + total.length + ')';
    } else if (checked.length === 0) {
        btn.textContent = btn.dataset.i18nPlaceholder ? __(btn.dataset.i18nPlaceholder) : (btn.dataset.placeholder || __('config.mcp.intentPlaceholder'));
    } else {
        const names = Array.from(checked).map(cb => {
            const label = cb.closest('label');
            return label ? label.textContent.trim() : cb.value;
        });
        btn.textContent = names.join(', ');
    }
}

async function loadMCPServers() {
    const [configResult, mcpResult] = await Promise.all([
        apiCall('config.get'),
        apiCall('mcp.servers'),
    ]);

    if (!configResult.success) return;

    const mcpServers = configResult.config.mcp_servers || {};
    const status = mcpResult.success ? (mcpResult.status || {}) : {};
    mcpStatusCache = status;
    mcpServersCache = mcpServers;

    // Built-in: SearXNG
    const searxng = mcpServers.searxng || {};
    document.getElementById('mcpSearxngEnabled').checked = searxng.enabled !== false;
    document.getElementById('mcpSearxngUrl').value = (searxng.env && searxng.env.SEARXNG_BASE_URL) || '';
    document.getElementById('mcpSearxngMaxResults').value = (searxng.env && searxng.env.SEARXNG_MAX_RESULTS) || 10;

    // Built-in: Image Search
    const img = mcpServers.image_search || {};
    document.getElementById('mcpImageSearchEnabled').checked = img.enabled !== false;
    document.getElementById('mcpImageSearchProvider').value = (img.env && img.env.IMAGE_PROVIDER) || 'pexels';
    document.getElementById('mcpImageSearchPexelsKey').value = (img.env && img.env.PEXELS_API_KEY) || '';
    document.getElementById('mcpImageSearchUnsplashKey').value = (img.env && img.env.UNSPLASH_API_KEY) || '';

    renderCustomMCPServers(mcpServers);
}

function renderCustomMCPServers(mcpServers) {
    const customNames = Object.keys(mcpServers).filter(n => n !== 'searxng' && n !== 'image_search');
    const tbody = document.getElementById('mcpCustomServersTable');
    if (customNames.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">' + __('config.mcp.noCustom') + '</td></tr>';
        return;
    }
    tbody.innerHTML = customNames.map(name => {
        const s = mcpServers[name];
        const st = mcpStatusCache[name] || {};
        const connected = st.connected ? __('config.mcp.connected') : __('config.mcp.disconnected');
        const statusClass = st.connected ? 'mcp-status-connected' : 'mcp-status-disconnected';
        const toolsCount = st.tools_count || 0;
        const addr = s.type === 'server' ? (s.url || '-') : (s.command || '') + ' ' + (s.args || []).join(' ');
        return `<tr>
            <td><code>${name}</code></td>
            <td>${s.type === 'server' ? 'Server' : 'Local'}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${addr}">${addr}</td>
            <td><span class="${statusClass}">${connected}</span></td>
            <td>${toolsCount}</td>
            <td>
                <button class="btn btn-sm btn-outline" onclick="editCustomMCPServer('${name}')">${__('config.mcp.edit')}</button>
                <button class="btn btn-sm btn-danger" onclick="deleteCustomMCPServer('${name}')">${__('config.mcp.delete')}</button>
            </td>
        </tr>`;
    }).join('');
}

function initMCPStatusStream() {
    if (window.__mcpStatusStream) return;
    const es = new EventSource('/api/mcp-status-stream');
    window.__mcpStatusStream = es;
    es.onmessage = (event) => {
        let payload;
        try {
            payload = JSON.parse(event.data);
        } catch (e) {
            return;
        }
        if (payload.type === 'snapshot') {
            mcpStatusCache = payload.status || {};
        } else if (payload.type === 'update') {
            mcpStatusCache[payload.name] = {
                connected: payload.connected,
                tools_count: payload.tools_count,
            };
        }
        renderCustomMCPServers(mcpServersCache);
    };
    es.onerror = () => {
        es.close();
        window.__mcpStatusStream = null;
        setTimeout(initMCPStatusStream, 3000);
    };
}

// ── Built-in MCP: SearXNG ──────────────────────────────────

async function saveBuiltinMCPSearxng() {
    const config = {
        type: 'local',
        enabled: document.getElementById('mcpSearxngEnabled').checked,
        command: 'python3',
        args: ['mcp/searxng_server.py'],
        env: {
            SEARXNG_BASE_URL: document.getElementById('mcpSearxngUrl').value || 'http://localhost:8888',
            SEARXNG_MAX_RESULTS: String(parseInt(document.getElementById('mcpSearxngMaxResults').value) || 10),
        },
    };
    const result = await apiCall('mcp.servers.save', { name: 'searxng', ...config });
    if (result.success) {
        showToast(__('config.mcp.srcSaveSuccess'), 'success');
        loadMCPServers();
        loadPlugins(); // Refresh plugin list to reflect MCP tool changes
    } else {
        showToast(__('config.mcp.saveFailed') + (result.error || __('config.llm.unknownError')), 'error');
    }
}

// ── Built-in MCP: Image Search ─────────────────────────────

async function saveBuiltinMCPImageSearch() {
    const config = {
        type: 'local',
        enabled: document.getElementById('mcpImageSearchEnabled').checked,
        command: 'python3',
        args: ['mcp/image_search_server.py'],
        env: {
            IMAGE_PROVIDER: document.getElementById('mcpImageSearchProvider').value,
            PEXELS_API_KEY: document.getElementById('mcpImageSearchPexelsKey').value,
            UNSPLASH_API_KEY: document.getElementById('mcpImageSearchUnsplashKey').value,
        },
    };
    const result = await apiCall('mcp.servers.save', { name: 'image_search', ...config });
    if (result.success) {
        showToast(__('config.mcp.imgSaveSuccess'), 'success');
        loadMCPServers();
        loadPlugins(); // Refresh plugin list to reflect MCP tool changes
    } else {
        showToast(__('config.mcp.saveFailed') + (result.error || __('config.llm.unknownError')), 'error');
    }
}

// ── Built-in MCP: Test Connection ──────────────────────────

async function testBuiltinMCP(name) {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = __('config.mcp.testingLabel');
    const resultEl = document.getElementById(name === 'searxng' ? 'mcpSearxngTestResult' : 'mcpImageSearchTestResult');

    let config;
    if (name === 'searxng') {
        config = {
            type: 'local', command: 'python3', args: ['mcp/searxng_server.py'],
            env: {
                SEARXNG_BASE_URL: document.getElementById('mcpSearxngUrl').value || 'http://localhost:8888',
                SEARXNG_MAX_RESULTS: String(parseInt(document.getElementById('mcpSearxngMaxResults').value) || 10),
            },
        };
    } else {
        config = {
            type: 'local', command: 'python3', args: ['mcp/image_search_server.py'],
            env: {
                IMAGE_PROVIDER: document.getElementById('mcpImageSearchProvider').value,
                PEXELS_API_KEY: document.getElementById('mcpImageSearchPexelsKey').value,
                UNSPLASH_API_KEY: document.getElementById('mcpImageSearchUnsplashKey').value,
            },
        };
    }

    const result = await apiCall('mcp.test', { name, config });
    if (result.success && result.result && result.result.connected) {
        const tools = (result.result.tools || []).map(t => t.name).join(', ');
        resultEl.innerHTML = __('config.mcp.testSuccess') + ` (${__('config.mcp.tools')}: ${tools})`;
        resultEl.style.color = 'green';
    } else {
        resultEl.innerHTML = __('config.mcp.testFailed') + ((result.result && result.result.error) || result.error || __('config.llm.unknownError'));
        resultEl.style.color = 'red';
    }
    btn.disabled = false;
    btn.textContent = __('config.mcp.test');
}

function showAddMCPServer() {
    currentEditServer = null;
    document.getElementById('mcpModalTitle').textContent = __('config.mcp.addTitle');
    document.getElementById('mcpServerName').value = '';
    document.getElementById('mcpServerType').value = 'server';
    document.getElementById('mcpServerUrl').value = '';
    document.getElementById('mcpServerCommand').value = '';
    document.getElementById('mcpServerArgs').value = '';
    document.getElementById('mcpServerEnv').value = '';
    document.getElementById('mcpServerEnabled').checked = true;
    onMCPServerTypeChange();
    document.getElementById('mcpCustomTestResult').textContent = '';
    document.getElementById('mcpServerModal').style.display = 'flex';
}

function closeMCPServerModal() {
    document.getElementById('mcpServerModal').style.display = 'none';
    currentEditServer = null;
}

function onMCPServerTypeChange() {
    const type = document.getElementById('mcpServerType').value;
    document.getElementById('mcpServerUrlGroup').style.display = type === 'server' ? 'block' : 'none';
    document.getElementById('mcpServerLocalGroup').style.display = type === 'local' ? 'block' : 'none';
}

function editCustomMCPServer(name) {
    currentEditServer = name;
    document.getElementById('mcpModalTitle').textContent = __('config.mcp.editTitle') + name;
    document.getElementById('mcpServerName').value = name;
    document.getElementById('mcpServerName').readOnly = true;
    document.getElementById('mcpCustomTestResult').textContent = '';
    document.getElementById('mcpServerModal').style.display = 'flex';

    // Load current config
    apiCall('config.get').then(result => {
        if (!result.success) return;
        const servers = result.config.mcp_servers || {};
        const s = servers[name];
        if (!s) return;
        document.getElementById('mcpServerType').value = s.type || 'server';
        onMCPServerTypeChange();
        document.getElementById('mcpServerUrl').value = s.url || '';
        document.getElementById('mcpServerCommand').value = s.command || '';
        document.getElementById('mcpServerArgs').value = (s.args || []).join('\n');
        document.getElementById('mcpServerEnabled').checked = s.enabled !== false;

        // Env
        const env = s.env || {};
        document.getElementById('mcpServerEnv').value = Object.entries(env).map(([k, v]) => `${k}=${v}`).join('\n');

    });
}

async function saveCustomMCPServer() {
    const name = document.getElementById('mcpServerName').value.trim();
    if (!name) { showToast(__('config.mcp.enterName'), 'error'); return; }

    const type = document.getElementById('mcpServerType').value;
    const config = {
        type: type,
        enabled: document.getElementById('mcpServerEnabled').checked,
    };

    if (type === 'server') {
        config.url = document.getElementById('mcpServerUrl').value;
    } else {
        config.command = document.getElementById('mcpServerCommand').value;
        config.args = document.getElementById('mcpServerArgs').value.split('\n').filter(s => s.trim());
        const envText = document.getElementById('mcpServerEnv').value;
        const env = {};
        envText.split('\n').filter(s => s.trim()).forEach(line => {
            const idx = line.indexOf('=');
            if (idx > 0) {
                env[line.substring(0, idx).trim()] = line.substring(idx + 1).trim();
            }
        });
        config.env = env;
    }

    const result = await apiCall('mcp.servers.save', { name, ...config });
    if (result.success) {
        showToast(__('config.mcp.saved', {name}), 'success');
        closeMCPServerModal();
        loadMCPServers();
        loadPlugins(); // Refresh plugin list to reflect new MCP tools
    } else {
        showToast(__('config.mcp.saveFailed') + (result.error || __('config.llm.unknownError')), 'error');
    }
}

async function deleteCustomMCPServer(name) {
    const confirmed = await showConfirmDialog(
        __('config.mcp.confirmDeleteTitle'),
        __('config.mcp.confirmDelete', {name})
    );
    if (!confirmed) return;
    const result = await apiCall('mcp.servers.delete', { name });
    if (result.success) {
        showToast(__('config.mcp.deleted', {name}), 'success');
        loadMCPServers();
        loadPlugins(); // Refresh plugin list to remove deleted MCP tools
    } else {
        showToast(__('config.mcp.deleteFailed'), 'error');
    }
}

async function testCustomMCP() {
    const name = document.getElementById('mcpServerName').value.trim() || 'test-server';
    const type = document.getElementById('mcpServerType').value;
    const config = { type, enabled: true };

    if (type === 'server') {
        config.url = document.getElementById('mcpServerUrl').value;
    } else {
        config.command = document.getElementById('mcpServerCommand').value;
        config.args = document.getElementById('mcpServerArgs').value.split('\n').filter(s => s.trim());
        const envText = document.getElementById('mcpServerEnv').value;
        const env = {};
        envText.split('\n').filter(s => s.trim()).forEach(line => {
            const idx = line.indexOf('=');
            if (idx > 0) env[line.substring(0, idx).trim()] = line.substring(idx + 1).trim();
        });
        config.env = env;
    }

    if ((type === 'server' && !config.url) || (type === 'local' && !config.command)) {
        showToast(__('config.mcp.fillInfo'), 'error');
        return;
    }

    const resultEl = document.getElementById('mcpCustomTestResult');
    resultEl.textContent = __('config.mcp.testingLabel');

    const result = await apiCall('mcp.test', { name, config });
    if (result.success && result.result && result.result.connected) {
        const tools = (result.result.tools || []).map(t => t.name).join(', ');
        resultEl.innerHTML = __('config.mcp.testSuccess') + ` (${result.result.tools_count} tools: ${tools})`;
        resultEl.style.color = 'green';
    } else {
        resultEl.innerHTML = __('config.mcp.testFailed') + ((result.result && result.result.error) || result.error || __('config.llm.unknownError'));
        resultEl.style.color = 'red';
    }
}

// ============================================================
// Intents Config
// ============================================================

async function toggleIntent(intentId) {
    const result = await apiCall('config.update', {
        settings: { [`intents.${intentId}.enabled`]: false },
    });
    const configResult = await apiCall('config.get');
    if (configResult.success) {
        const current = configResult.config.intents?.[intentId]?.enabled;
        await apiCall('config.update', {
            settings: { [`intents.${intentId}.enabled`]: !current },
        });
    }
    loadIntents();
    showToast(__('config.intents.statusUpdated'), 'success');
}

async function deleteIntent(intentId) {
    const confirmed = await showConfirmDialog(
        __('config.intents.confirmDeleteTitle'),
        __('config.intents.confirmDelete', {id: intentId})
    );
    if (!confirmed) return;
    const result = await apiCall('intents.delete', { intent_type: intentId });
    if (result.success) { loadIntents(); showToast(__('config.intents.deleted'), 'success'); }
    else { showToast(__('config.intents.deleteFailed'), 'error'); }
}

let editingIntentId = null;

function openEditIntent(intentId) {
    editingIntentId = intentId;
    const intent = registeredIntents[intentId] || {};
    document.getElementById('editIntentId').value = intentId;
    document.getElementById('editIntentName').value = intent.name || '';
    document.getElementById('editIntentDesc').value = intent.description || '';
    document.getElementById('editIntentPlanningPrompt').value = intent.planning_prompt || '';
    document.getElementById('editIntentNodePrompt').value = intent.node_prompt || '';
    document.getElementById('editIntentFinalizerPrompt').value = intent.finalizer_prompt || '';
    document.getElementById('intentEditModal').style.display = 'flex';
}

function closeEditIntentModal() {
    document.getElementById('intentEditModal').style.display = 'none';
}

async function saveEditIntent() {
    const id = editingIntentId;
    const name = document.getElementById('editIntentName').value.trim();
    if (!name) { showToast(__('config.intents.regRequired'), 'error'); return; }
    const params = {
        intent_type: id,
        enabled: registeredIntents[id]?.enabled !== false,
        name,
        description: document.getElementById('editIntentDesc').value.trim(),
    };
    const planningPrompt = document.getElementById('editIntentPlanningPrompt').value.trim();
    if (planningPrompt) params.planning_prompt = planningPrompt;
    const nodePrompt = document.getElementById('editIntentNodePrompt').value.trim();
    if (nodePrompt) params.node_prompt = nodePrompt;
    const finalizerPrompt = document.getElementById('editIntentFinalizerPrompt').value.trim();
    if (finalizerPrompt) params.finalizer_prompt = finalizerPrompt;
    const result = await apiCall('intents.update', params);
    if (result.success) {
        closeEditIntentModal();
        loadIntents();
        showToast(__('config.intents.editSuccess'), 'success');
    } else { showToast(__('config.intents.editFailed'), 'error'); }
}

async function registerIntent() {
    const id = document.getElementById('newIntentId').value.trim();
    const name = document.getElementById('newIntentName').value.trim();
    const desc = document.getElementById('newIntentDesc').value.trim();
    if (!id || !name) { showToast(__('config.intents.regRequired'), 'error'); return; }
    const intentsResult = await apiCall('intents.get');
    if (intentsResult.success && intentsResult.intents?.[id]) {
        showToast(__('config.intents.idExists'), 'error'); return;
    }
    const params = { intent_type: id, enabled: true, name, description: desc };
    const planningPrompt = document.getElementById('newIntentPlanningPrompt').value.trim();
    if (planningPrompt) params.planning_prompt = planningPrompt;
    const nodePrompt = document.getElementById('newIntentNodePrompt').value.trim();
    if (nodePrompt) params.node_prompt = nodePrompt;
    const finalizerPrompt = document.getElementById('newIntentFinalizerPrompt').value.trim();
    if (finalizerPrompt) params.finalizer_prompt = finalizerPrompt;
    const result = await apiCall('intents.update', params);
    if (result.success) {
        showToast(__('config.intents.regSuccess'), 'success');
        document.getElementById('newIntentId').value = '';
        document.getElementById('newIntentName').value = '';
        document.getElementById('newIntentDesc').value = '';
        document.getElementById('newIntentPlanningPrompt').value = '';
        document.getElementById('newIntentNodePrompt').value = '';
        document.getElementById('newIntentFinalizerPrompt').value = '';
        loadIntents();
    } else { showToast(__('config.intents.regFailed'), 'error'); }
}

// ============================================================
// Server Config
// ============================================================

function onLanguageChange() {
    const lang = document.getElementById('languageSelect').value;
    if (typeof setLanguage === 'function') {
        setLanguage(lang);
    } else {
        apiCall('config.update', {
            settings: { 'server.language': lang }
        });
    }
}

async function saveServerConfig() {
    const selectedLang = document.getElementById('languageSelect').value || 'zh-CN';
    const serverConfig = {
        rpc_port: parseInt(document.getElementById('servicePort').value) || 11555,
        admin_port: parseInt(document.getElementById('adminPort').value) || 11556,
        host: document.getElementById('serverHost').value || '0.0.0.0',
        debug: true,
        language: selectedLang,
        node_parallel_count: parseInt(document.getElementById('nodeParallelCount').value) || 1,
        log_file: document.getElementById('serverLogFile').value || 'debugout.log',
        proxy: document.getElementById('serverProxy').value.trim() || 'http://192.168.10.2:7890',
    };
    const defaultCity = document.getElementById('defaultLocationCity').value.trim() || 'Nanjing';
    await apiCall('config.update', { settings: { 'default_location.city': defaultCity } });
    const result = await apiCall('config.update', { section: 'server', values: serverConfig });
    if (result.success) {
        showToast(__('config.server.saveSuccess'), 'success');
        if (typeof i18nLoadLocale === 'function') {
            i18nLoadLocale(selectedLang);
        }
    } else {
        showToast(__('config.server.saveFailed'), 'error');
    }
}

// ============================================================
// Plugin Tools Management
// ============================================================

let allPlugins = [];
let currentPluginDetail = null;

function sourceBadgeClass(source) {
    if (source === 'MCP (内置)') return 'badge-source-mcp-builtin';
    if (source === 'MCP (外部)') return 'badge-source-mcp-external';
    if (source === '外部插件') return 'badge-source-external';
    return 'badge-source-internal';
}

function formatSourceLabel(tool) {
    const source = tool.source || '内部插件';
    if (source === 'MCP (外部)' && tool.server_name) {
        return source + ' - ' + tool.server_name;
    }
    return source;
}

async function loadPlugins() {
    const result = await apiCall('plugins.get');
    const tbody = document.getElementById('pluginsTable');
    if (!result.success || !result.tools) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center">' + __('config.plugins.loadFailed') + '</td></tr>';
        return;
    }
    allPlugins = result.tools;
    document.getElementById('pluginsTotalCount').textContent =
        __('config.plugins.totalTools', { count: allPlugins.length });

    const filterSelect = document.getElementById('pluginIntentFilter');
    const existingOptions = filterSelect.querySelector('option[value="all"]');
    filterSelect.innerHTML = '';
    filterSelect.appendChild(existingOptions);
    (result.intents || []).forEach(intent => {
        const opt = document.createElement('option');
        opt.value = intent;
        opt.textContent = intent;
        filterSelect.appendChild(opt);
    });

    renderPluginsTable(allPlugins);
}

function renderPluginsTable(tools) {
    const tbody = document.getElementById('pluginsTable');
    if (!tools || tools.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">' + __('config.plugins.none') + '</td></tr>';
        return;
    }
    tbody.innerHTML = tools.map(tool => {
        const intents = (tool.intents || []).join(', ') || __('config.plugins.noIntents');
        const source = tool.source || '内部插件';
        const sourceLabel = formatSourceLabel(tool);
        return `
        <tr>
            <td><code>${tool.name}</code></td>
            <td><span class="badge badge-info">${intents}</span></td>
            <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${tool.description}">${tool.description}</td>
            <td><span class="badge ${sourceBadgeClass(source)}">${sourceLabel}</span></td>
            <td><span class="badge ${tool.enabled ? 'badge-success' : 'badge-danger'}">${tool.enabled ? __('config.plugins.enabled') : __('config.plugins.disabled')}</span></td>
            <td>
                <button class="btn btn-sm btn-outline" onclick="showPluginDetail('${tool.name}')">${__('config.plugins.details')}</button>
                <button class="btn btn-sm ${tool.enabled ? 'btn-danger' : 'btn-primary'}" onclick="togglePlugin('${tool.name}', ${!tool.enabled})">${tool.enabled ? __('config.plugins.disable') : __('config.plugins.enable')}</button>
            </td>
        </tr>
    `}).join('');
}

function filterPlugins() {
    const intent = document.getElementById('pluginIntentFilter').value;
    const source = document.getElementById('pluginSourceFilter').value;
    const search = document.getElementById('pluginSearchInput').value.toLowerCase();
    let filtered = allPlugins;
    if (intent !== 'all') {
        filtered = filtered.filter(t => (t.intents || []).includes(intent));
    }
    if (source !== 'all') {
        filtered = filtered.filter(t => t.source === source);
    }
    if (search) {
        filtered = filtered.filter(t =>
            t.name.toLowerCase().includes(search) ||
            t.description.toLowerCase().includes(search)
        );
    }
    renderPluginsTable(filtered);
}

async function togglePlugin(name, enabled) {
    const result = await apiCall('plugins.toggle', { tool_name: name, enabled });
    if (result.success) {
        showToast(__('config.plugins.toggleSuccess', { name }), 'success');
        loadPlugins();
    } else {
        showToast(__('config.plugins.toggleFailed') + (result.error || ''), 'error');
    }
}

function showPluginDetail(name) {
    const tool = allPlugins.find(t => t.name === name);
    if (!tool) return;
    currentPluginDetail = tool;

    document.getElementById('pluginDetailTitle').textContent = tool.name;
    document.getElementById('pluginDetailDesc').textContent = tool.description;

    const sourceBadge = document.getElementById('pluginDetailSource');
    const pluginSource = tool.source || '内部插件';
    sourceBadge.textContent = formatSourceLabel(tool);
    sourceBadge.className = 'badge ' + sourceBadgeClass(pluginSource);

    const statusBadge = document.getElementById('pluginDetailStatus');
    statusBadge.textContent = tool.enabled ? __('config.plugins.enabled') : __('config.plugins.disabled');
    statusBadge.className = 'badge ' + (tool.enabled ? 'badge-success' : 'badge-danger');

    // Set intent checkboxes in detail modal
    const toolIntents = tool.intents || [];
    const selectAll = toolIntents.length === 0;
    document.querySelectorAll('#pluginDetailIntentCheckboxes .intent-checkbox').forEach(cb => {
        cb.checked = selectAll || toolIntents.includes(cb.value);
    });
    updateMultiselectLabel('pluginDetailIntentCheckboxes');

    const paramsDiv = document.getElementById('pluginDetailParams');
    paramsDiv.textContent = JSON.stringify(tool.parameters, null, 2);

    const toggleBtn = document.getElementById('pluginDetailToggleBtn');
    toggleBtn.textContent = tool.enabled ? __('config.plugins.disable') : __('config.plugins.enable');
    toggleBtn.className = 'btn ' + (tool.enabled ? 'btn-danger' : 'btn-primary');

    document.getElementById('pluginDetailModal').style.display = 'flex';
}

function closePluginDetail() {
    document.getElementById('pluginDetailModal').style.display = 'none';
    currentPluginDetail = null;
}

async function togglePluginFromDetail() {
    if (!currentPluginDetail) return;
    await togglePlugin(currentPluginDetail.name, !currentPluginDetail.enabled);
    closePluginDetail();
}

async function savePluginIntents() {
    if (!currentPluginDetail) return;
    const selected = getSelectedIntents('pluginDetailIntentCheckboxes');
    const result = await apiCall('plugins.intents', { tool_name: currentPluginDetail.name, intents: selected });
    if (result.success) {
        showToast(__('config.plugins.intentsSaved', { name: currentPluginDetail.name }), 'success');
        currentPluginDetail.intents = selected;
        const idx = allPlugins.findIndex(t => t.name === currentPluginDetail.name);
        if (idx !== -1) {
            allPlugins[idx].intents = selected;
        }
        loadPlugins();
    } else {
        showToast(__('config.plugins.intentsSaveFailed') + (result.error || ''), 'error');
    }
}

// ============================================================
// Cron（定时任务）
// ============================================================

let cronTasksCache = [];
let cronResultsCache = [];
let editingCronId = null;
let cronClockOffsetMs = null;
let cronClockTimer = null;
let cronOutputChannelsCache = [];

async function loadCronTasks() {
    const result = await apiCall('cron.list');
    const tbody = document.getElementById('cronTasksTable');
    if (!result.success) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center">' + __('config.cron.loadFailed') + (result.error || '') + '</td></tr>';
        return;
    }
    renderCronScheduler(result.scheduler || {});
    startCronClock(result.server_time);
    cronTasksCache = result.tasks || [];
    renderCronTasks(cronTasksCache, result.next_runs || {});
    cronOutputChannelsCache = result.output_channels || [];
    renderCronOutputChannelOptions();
}

function renderCronOutputChannelOptions() {
    const container = document.getElementById('cronOutputChannelsCheckboxes');
    if (!container || container.dataset.rendered) return;
    if (!cronOutputChannelsCache.length) {
        container.innerHTML = '<span class="text-muted">' + __('config.cron.noOutputChannels') + '</span>';
        return;
    }
    container.innerHTML = cronOutputChannelsCache.map(c => `
        <label>
            <input type="checkbox" class="intent-checkbox" data-container="cronOutputChannelsCheckboxes" value="${c.id}" onchange="updateMultiselectLabel('cronOutputChannelsCheckboxes')">
            ${c.label || c.id}
        </label>
    `).join('');
    container.dataset.rendered = '1';
    updateMultiselectLabel('cronOutputChannelsCheckboxes');
}

function setCronOutputChannelSelection(keys) {
    const container = document.getElementById('cronOutputChannelsCheckboxes');
    if (!container) return;
    container.querySelectorAll('.intent-checkbox').forEach(cb => {
        cb.checked = (keys || []).includes(cb.value);
    });
    updateMultiselectLabel('cronOutputChannelsCheckboxes');
}

function startCronClock(serverTime) {
    const el = document.getElementById('cronServerTimeInfo');
    if (!el || !serverTime) return;
    const parsed = new Date(String(serverTime).replace(' ', 'T'));
    if (isNaN(parsed.getTime())) return;
    cronClockOffsetMs = parsed.getTime() - Date.now();
    if (cronClockTimer) clearInterval(cronClockTimer);
    const pad = n => String(n).padStart(2, '0');
    const tick = () => {
        const now = new Date(Date.now() + cronClockOffsetMs);
        el.textContent = `${__('config.cron.serverTime')}: ` +
            `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
            `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    };
    tick();
    cronClockTimer = setInterval(tick, 1000);
}

function renderCronScheduler(s) {
    const started = s.status === 'started';
    const badge = document.getElementById('cronSchedulerBadge');
    badge.textContent = started ? __('config.cron.started') : __('config.cron.stopped');
    badge.className = 'badge ' + (started ? 'badge-success' : 'badge-secondary');
    document.getElementById('cronStartBtn').style.display = started ? 'none' : '';
    document.getElementById('cronStopBtn').style.display = started ? '' : 'none';
    document.getElementById('cronNextRunInfo').textContent =
        s.next_run ? `${__('config.cron.nextRun')}: ${s.next_run}` : '';
    document.getElementById('cronErrorInfo').textContent = s.error || '';
}

function cronScheduleText(task) {
    if (task.repeat === 'weekly') {
        return __('config.cron.schedWeekly', { wd: __('config.cron.wd' + task.weekday), time: task.time });
    }
    if (task.repeat === 'monthly') {
        return __('config.cron.schedMonthly', { dom: task.day_of_month, time: task.time });
    }
    return __('config.cron.schedDaily', { time: task.time });
}

function renderCronTasks(tasks, nextRuns) {
    const tbody = document.getElementById('cronTasksTable');
    if (!tasks.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">' + __('config.cron.noTasks') + '</td></tr>';
        return;
    }
    const sorted = [...tasks].sort((a, b) => (a.time || '').localeCompare(b.time || ''));
    tbody.innerHTML = sorted.map(t => {
        const enabled = t.enabled !== false;
        const typeLabel = t.task_type === 'agent' ? __('config.cron.typeAgentShort') : __('config.cron.typeSystemShort');
        return `
        <tr>
            <td><strong>${t.title}</strong><br><small class="text-muted">${t.description}</small></td>
            <td>${cronScheduleText(t)}</td>
            <td><span class="badge badge-info">${typeLabel}</span></td>
            <td><span class="badge ${enabled ? 'badge-success' : 'badge-danger'}">${enabled ? __('config.cron.enabled') : __('config.cron.disabled')}</span></td>
            <td>${nextRuns[t.id] || '-'}</td>
            <td>
                <button class="btn btn-sm btn-outline" onclick="editCronTask('${t.id}')">${__('config.cron.edit')}</button>
                <button class="btn btn-sm btn-outline" onclick="toggleCronTask('${t.id}', ${!enabled})">${enabled ? __('config.cron.disableAction') : __('config.cron.enableAction')}</button>
                <button class="btn btn-sm btn-outline" onclick="showCronResults('${t.id}')">${__('config.cron.viewResults')}</button>
                <button class="btn btn-sm btn-danger" onclick="deleteCronTask('${t.id}')">${__('config.mcp.delete')}</button>
            </td>
        </tr>`;
    }).join('');
}

async function cronStart() {
    const result = await apiCall('cron.start');
    if (result.success) {
        showToast(__('config.cron.started'), 'success');
    } else {
        showToast(__('config.cron.opFailed') + (result.error || ''), 'error');
    }
    loadCronTasks();
}

async function cronStop() {
    const result = await apiCall('cron.stop');
    if (result.success) {
        showToast(__('config.cron.stopped'), 'success');
    } else {
        showToast(__('config.cron.opFailed') + (result.error || ''), 'error');
    }
    loadCronTasks();
}

function showAddCronTask() {
    editingCronId = null;
    document.getElementById('cronModalTitle').textContent = __('config.cron.addTitle');
    document.getElementById('cronTitle').value = '';
    document.getElementById('cronTime').value = '';
    document.getElementById('cronRepeat').value = 'daily';
    document.getElementById('cronWeekday').value = '0';
    document.getElementById('cronDayOfMonth').value = 1;
    document.getElementById('cronType').value = 'system';
    document.getElementById('cronDescription').value = '';
    document.getElementById('cronEnabled').checked = true;
    setCronOutputChannelSelection([]);
    onCronRepeatChange();
    onCronTypeChange();
    document.getElementById('cronTaskModal').style.display = 'flex';
}

function editCronTask(id) {
    const t = cronTasksCache.find(x => x.id === id);
    if (!t) return;
    editingCronId = id;
    document.getElementById('cronModalTitle').textContent = __('config.cron.editTitle');
    document.getElementById('cronTitle').value = t.title || '';
    document.getElementById('cronTime').value = t.time || '';
    document.getElementById('cronRepeat').value = t.repeat || 'daily';
    document.getElementById('cronWeekday').value = String(t.weekday ?? 0);
    document.getElementById('cronDayOfMonth').value = t.day_of_month ?? 1;
    document.getElementById('cronType').value = t.task_type || 'system';
    document.getElementById('cronDescription').value = t.description || '';
    document.getElementById('cronEnabled').checked = t.enabled !== false;
    setCronOutputChannelSelection(t.output_channels || []);
    onCronRepeatChange();
    onCronTypeChange();
    document.getElementById('cronTaskModal').style.display = 'flex';
}

function closeCronTaskModal() {
    document.getElementById('cronTaskModal').style.display = 'none';
    editingCronId = null;
}

function onCronRepeatChange() {
    const repeat = document.getElementById('cronRepeat').value;
    document.getElementById('cronWeekdayGroup').style.display = repeat === 'weekly' ? 'block' : 'none';
    document.getElementById('cronDayOfMonthGroup').style.display = repeat === 'monthly' ? 'block' : 'none';
}

function onCronTypeChange() {
    const isAgent = document.getElementById('cronType').value === 'agent';
    document.getElementById('cronDescLabel').textContent = isAgent ? __('config.cron.descAgent') : __('config.cron.descSystem');
    document.getElementById('cronDescription').placeholder = isAgent ? __('config.cron.phAgent') : __('config.cron.phSystem');
}

async function saveCronTask() {
    const title = document.getElementById('cronTitle').value.trim();
    const time = document.getElementById('cronTime').value.trim();
    const description = document.getElementById('cronDescription').value.trim();
    if (!title) { showToast(__('config.cron.titleRequired'), 'error'); return; }
    if (!/^\d{1,2}:\d{2}$/.test(time)) { showToast(__('config.cron.invalidTime'), 'error'); return; }
    if (!description) { showToast(__('config.cron.descRequired'), 'error'); return; }

    const params = {
        title,
        time,
        repeat: document.getElementById('cronRepeat').value,
        weekday: parseInt(document.getElementById('cronWeekday').value, 10),
        day_of_month: parseInt(document.getElementById('cronDayOfMonth').value, 10),
        task_type: document.getElementById('cronType').value,
        description,
        enabled: document.getElementById('cronEnabled').checked,
        output_channels: getSelectedIntents('cronOutputChannelsCheckboxes'),
    };

    const result = editingCronId
        ? await apiCall('cron.update', { id: editingCronId, ...params })
        : await apiCall('cron.create', params);
    if (result.success) {
        showToast(__('config.cron.saved'), 'success');
        closeCronTaskModal();
        loadCronTasks();
    } else {
        showToast(__('config.cron.saveFailed') + (result.error || ''), 'error');
    }
}

async function toggleCronTask(id, enable) {
    const result = await apiCall('cron.update', { id, enabled: enable });
    if (result.success) {
        showToast(__('config.cron.saved'), 'success');
        loadCronTasks();
    } else {
        showToast(__('config.cron.opFailed') + (result.error || ''), 'error');
    }
}

async function deleteCronTask(id) {
    const task = cronTasksCache.find(x => x.id === id);
    const confirmed = await showConfirmDialog(
        __('config.cron.confirmDeleteTitle'),
        __('config.cron.confirmDeleteMsg', { title: task ? task.title : id })
    );
    if (!confirmed) return;
    const result = await apiCall('cron.delete', { id });
    if (result.success) {
        showToast(__('config.cron.deleted'), 'success');
        loadCronTasks();
    } else {
        showToast(__('config.cron.deleteFailed') + (result.error || ''), 'error');
    }
}

// ── Cron Run Results ───────────────────────────────────────

async function showCronResults(cronId) {
    document.getElementById('cronResultDetail').style.display = 'none';
    document.getElementById('cronResultsModal').style.display = 'flex';
    await loadCronResults(cronId);
}

async function loadCronResults(cronId) {
    const result = await apiCall('cron.results', cronId ? { cron_id: cronId } : {});
    const tbody = document.getElementById('cronResultsTable');
    if (!result.success) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center">' + __('config.cron.loadFailed') + (result.error || '') + '</td></tr>';
        return;
    }
    cronResultsCache = result.results || [];
    if (!cronResultsCache.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">' + __('config.cron.noResults') + '</td></tr>';
        return;
    }
    tbody.innerHTML = cronResultsCache.map(r => {
        const ok = r.status === 'success';
        const duration = r.duration_ms >= 1000
            ? (r.duration_ms / 1000).toFixed(1) + 's'
            : (r.duration_ms || 0) + 'ms';
        return `
        <tr>
            <td>${r.title}</td>
            <td>${r.started_at}</td>
            <td>${duration}</td>
            <td><span class="badge ${ok ? 'badge-success' : 'badge-danger'}">${ok ? __('config.cron.statusSuccess') : __('config.cron.statusFailed')}</span></td>
            <td><button class="btn btn-sm btn-outline" onclick="showCronResultOutput('${r.result_id}')">${__('config.cron.viewOutput')}</button></td>
        </tr>`;
    }).join('');
}

function showCronResultOutput(resultId) {
    const r = cronResultsCache.find(x => x.result_id === resultId);
    if (!r) return;
    const parts = [];
    if (r.output) parts.push(r.output);
    if (r.error) parts.push('[error] ' + r.error);
    document.getElementById('cronResultDetailPre').textContent =
        parts.join('\n') || __('config.cron.noOutput');
    document.getElementById('cronResultDetail').style.display = 'block';
}

function closeCronResultsModal() {
    document.getElementById('cronResultsModal').style.display = 'none';
}
