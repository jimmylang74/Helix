/**
 * Configuration Management JavaScript
 * Handles LLM, MCP, intents, and server configuration
 */

let currentEditServer = null;

document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    setupTabs();
    loadIntents();
    loadMCPServers();
});

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

async function loadConfig() {
    const result = await apiCall('/admin/config');
    if (!result.success) return;

    const config = result.config;
    const llm = config.llm || {};
    const server = config.server || {};

    // LLM Provider
    document.getElementById('llmProvider').value = llm.provider || 'ollama';
    onProviderChange();
    const ollama = llm.ollama || {};
    document.getElementById('ollamaBaseUrl').value = ollama.base_url || '';
    document.getElementById('ollamaModel').value = ollama.model || '';
    document.getElementById('ollamaTemp').value = ollama.temperature || 0.7;
    document.getElementById('ollamaMaxTokens').value = ollama.max_tokens || 4096;
    const openai = llm.openai || {};
    document.getElementById('openaiApiKey').value = openai.api_key || '';
    document.getElementById('openaiBaseUrl').value = openai.base_url || '';
    document.getElementById('openaiModel').value = openai.model || '';
    const gemini = llm.gemini || {};
    document.getElementById('geminiApiKey').value = gemini.api_key || '';
    document.getElementById('geminiModel').value = gemini.model || '';
    const deepseek = llm.deepseek || {};
    document.getElementById('deepseekApiKey').value = deepseek.api_key || '';
    document.getElementById('deepseekBaseUrl').value = deepseek.base_url || '';
    document.getElementById('deepseekModel').value = deepseek.model || '';

    // Server
    document.getElementById('servicePort').value = server.service_port || 11555;
    document.getElementById('adminPort').value = server.admin_port || 11556;
    document.getElementById('serverHost').value = server.host || '0.0.0.0';
    const langSelect = document.getElementById('languageSelect');
    if (langSelect) langSelect.value = server.language || 'zh-CN';
}

function onProviderChange() {
    const provider = document.getElementById('llmProvider').value;
    document.getElementById('ollamaConfig').style.display = provider === 'ollama' ? 'block' : 'none';
    document.getElementById('openaiConfig').style.display = provider === 'openai' ? 'block' : 'none';
    document.getElementById('geminiConfig').style.display = provider === 'gemini' ? 'block' : 'none';
    document.getElementById('deepseekConfig').style.display = provider === 'deepseek' ? 'block' : 'none';
}

// ============================================================
// LLM Config
// ============================================================

function getLLMConfig() {
    return {
        provider: document.getElementById('llmProvider').value,
        ollama: {
            base_url: document.getElementById('ollamaBaseUrl').value,
            model: document.getElementById('ollamaModel').value,
            temperature: parseFloat(document.getElementById('ollamaTemp').value) || 0.7,
            max_tokens: parseInt(document.getElementById('ollamaMaxTokens').value) || 4096,
        },
        openai: {
            api_key: document.getElementById('openaiApiKey').value,
            base_url: document.getElementById('openaiBaseUrl').value,
            model: document.getElementById('openaiModel').value,
            temperature: 0.7, max_tokens: 4096,
        },
        gemini: {
            api_key: document.getElementById('geminiApiKey').value,
            model: document.getElementById('geminiModel').value,
            temperature: 0.7, max_tokens: 4096,
        },
        deepseek: {
            api_key: document.getElementById('deepseekApiKey').value,
            base_url: document.getElementById('deepseekBaseUrl').value,
            model: document.getElementById('deepseekModel').value,
            temperature: 0.7, max_tokens: 4096,
        },
    };
}

async function saveLLMConfig() {
    const result = await apiCall('/admin/config', 'POST', {
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
    const result = await apiCall('/admin/llm/test', 'POST');
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

// ============================================================
// MCP Config
// ============================================================

let registeredIntents = {};

async function loadIntents() {
    const result = await apiCall('/admin/intents');
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
        tbody.innerHTML = entries.map(([id, intent]) => `
            <tr>
                <td><code>${id}</code></td>
                <td>${intent.name || id}</td>
                <td>${intent.description || '-'}</td>
                <td><span class="badge ${intent.enabled ? 'badge-success' : 'badge-danger'}">${intent.enabled ? __('config.intents.enabled') : __('config.intents.disabled')}</span></td>
                <td>
                    <button class="btn btn-sm btn-outline" onclick="toggleIntent('${id}')">${intent.enabled ? __('config.intents.disable') : __('config.intents.enable')}</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteIntent('${id}')">${__('config.intents.deleteLabel')}</button>
                </td>
            </tr>
        `).join('');
    }
    renderIntentCheckboxes();
}

function renderIntentCheckboxes() {
    const entries = Object.entries(registeredIntents);
    const containers = ['mcpSearxngIntents', 'mcpImageSearchIntents', 'mcpServerIntentCheckboxes'];
    containers.forEach(containerId => {
        const container = document.getElementById(containerId);
        if (!container) return;
        if (entries.length === 0) {
            container.innerHTML = '<span class="text-muted">' + __('common.noData') + '</span>';
            return;
        }
        container.innerHTML = entries.map(([id, intent]) => `
            <label>
                <input type="checkbox" class="intent-checkbox" data-container="${containerId}" value="${id}" checked onchange="updateMultiselectLabel('${containerId}')">
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
        btn.textContent = __('config.mcp.allIntents') + ' (' + total.length + ')';
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
        apiCall('/admin/config'),
        apiCall('/admin/mcp/servers'),
    ]);

    if (!configResult.success) return;

    const mcpServers = configResult.config.mcp_servers || {};
    const status = mcpResult.success ? (mcpResult.status || {}) : {};

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

    // Set intent checkboxes for built-in servers
    setTimeout(() => {
        if (searxng.intent_categories) {
            document.querySelectorAll('#mcpSearxngIntents .intent-checkbox').forEach(cb => {
                cb.checked = searxng.intent_categories.includes(cb.value);
            });
        }
        if (img.intent_categories) {
            document.querySelectorAll('#mcpImageSearchIntents .intent-checkbox').forEach(cb => {
                cb.checked = img.intent_categories.includes(cb.value);
            });
        }
        updateMultiselectLabel('mcpSearxngIntents');
        updateMultiselectLabel('mcpImageSearchIntents');
    }, 100);

    // Custom servers table
    const customNames = Object.keys(mcpServers).filter(n => n !== 'searxng' && n !== 'image_search');
    const tbody = document.getElementById('mcpCustomServersTable');
    if (customNames.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">' + __('config.mcp.noCustom') + '</td></tr>';
        return;
    }
    tbody.innerHTML = customNames.map(name => {
        const s = mcpServers[name];
        const st = status[name] || {};
        const connected = st.connected ? __('config.mcp.connected') : __('config.mcp.disconnected');
        const toolsCount = st.tools_count || 0;
        const addr = s.type === 'server' ? (s.url || '-') : (s.command || '') + ' ' + (s.args || []).join(' ');
        const intents = (s.intent_categories || []).join(', ') || __('config.mcp.all');
        return `<tr>
            <td><code>${name}</code></td>
            <td>${s.type === 'server' ? 'Server' : 'Local'}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${addr}">${addr}</td>
            <td>${connected}</td>
            <td>${toolsCount}</td>
            <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${intents}">${intents}</td>
            <td>
                <button class="btn btn-sm btn-outline" onclick="editCustomMCPServer('${name}')">${__('config.mcp.edit')}</button>
                <button class="btn btn-sm btn-danger" onclick="deleteCustomMCPServer('${name}')">${__('config.mcp.delete')}</button>
            </td>
        </tr>`;
    }).join('');
}

// ── Built-in MCP: SearXNG ──────────────────────────────────

async function saveBuiltinMCPSearxng() {
    const config = {
        type: 'local',
        enabled: document.getElementById('mcpSearxngEnabled').checked,
        command: 'python3',
        args: ['mcp/searxng_server.py'],
        intent_categories: getSelectedIntents('mcpSearxngIntents'),
        env: {
            SEARXNG_BASE_URL: document.getElementById('mcpSearxngUrl').value || 'http://localhost:8888',
            SEARXNG_MAX_RESULTS: String(parseInt(document.getElementById('mcpSearxngMaxResults').value) || 10),
        },
    };
    const result = await apiCall('/admin/mcp/servers/searxng', 'POST', config);
    if (result.success) {
        showToast(__('config.mcp.srcSaveSuccess'), 'success');
        loadMCPServers();
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
        intent_categories: getSelectedIntents('mcpImageSearchIntents'),
        env: {
            IMAGE_PROVIDER: document.getElementById('mcpImageSearchProvider').value,
            PEXELS_API_KEY: document.getElementById('mcpImageSearchPexelsKey').value,
            UNSPLASH_API_KEY: document.getElementById('mcpImageSearchUnsplashKey').value,
        },
    };
    const result = await apiCall('/admin/mcp/servers/image_search', 'POST', config);
    if (result.success) {
        showToast(__('config.mcp.imgSaveSuccess'), 'success');
        loadMCPServers();
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

    const result = await apiCall('/admin/mcp/test', 'POST', { name, config });
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

// ── Custom MCP Server Modal ────────────────────────────────

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
    document.querySelectorAll('#mcpServerIntentCheckboxes .intent-checkbox').forEach(cb => cb.checked = true);
    updateMultiselectLabel('mcpServerIntentCheckboxes');
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
    apiCall('/admin/config').then(result => {
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

        // Intent checkboxes
        setTimeout(() => {
            const cats = s.intent_categories || [];
            document.querySelectorAll('#mcpServerIntentCheckboxes .intent-checkbox').forEach(cb => {
                cb.checked = cats.length === 0 || cats.includes(cb.value);
            });
            updateMultiselectLabel('mcpServerIntentCheckboxes');
        }, 100);
    });
}

async function saveCustomMCPServer() {
    const name = document.getElementById('mcpServerName').value.trim();
    if (!name) { showToast(__('config.mcp.enterName'), 'error'); return; }

    const type = document.getElementById('mcpServerType').value;
    const config = {
        type: type,
        enabled: document.getElementById('mcpServerEnabled').checked,
        intent_categories: getSelectedIntents('mcpServerIntentCheckboxes'),
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

    const result = await apiCall(`/admin/mcp/servers/${name}`, 'POST', config);
    if (result.success) {
        showToast(__('config.mcp.saved', {name}), 'success');
        closeMCPServerModal();
        loadMCPServers();
    } else {
        showToast(__('config.mcp.saveFailed') + (result.error || __('config.llm.unknownError')), 'error');
    }
}

async function deleteCustomMCPServer(name) {
    if (!confirm(__('config.mcp.confirmDelete', {name}))) return;
    const result = await apiCall(`/admin/mcp/servers/${name}`, 'DELETE');
    if (result.success) {
        showToast(__('config.mcp.deleted', {name}), 'success');
        loadMCPServers();
    } else {
        showToast(__('config.mcp.deleteFailed'), 'error');
    }
}

async function testCustomMCP() {
    const name = document.getElementById('mcpServerName').value.trim() || 'test-server';
    const type = document.getElementById('mcpServerType').value;
    const config = { type, enabled: true, intent_categories: [] };

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

    const result = await apiCall('/admin/mcp/test', 'POST', { name, config });
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
    const result = await apiCall('/admin/config', 'POST', {
        settings: { [`intents.${intentId}.enabled`]: false },
    });
    const configResult = await apiCall('/admin/config');
    if (configResult.success) {
        const current = configResult.config.intents?.[intentId]?.enabled;
        await apiCall('/admin/config', 'POST', {
            settings: { [`intents.${intentId}.enabled`]: !current },
        });
    }
    loadIntents();
    showToast(__('config.intents.statusUpdated'), 'success');
}

async function deleteIntent(intentId) {
    if (!confirm(__('config.intents.confirmDelete', {id: intentId}))) return;
    const result = await apiCall(`/admin/intents/${intentId}`, 'DELETE');
    if (result.success) { loadIntents(); showToast(__('config.intents.deleted'), 'success'); }
    else { showToast(__('config.intents.deleteFailed'), 'error'); }
}

async function registerIntent() {
    const id = document.getElementById('newIntentId').value.trim();
    const name = document.getElementById('newIntentName').value.trim();
    const desc = document.getElementById('newIntentDesc').value.trim();
    if (!id || !name) { showToast(__('config.intents.regRequired'), 'error'); return; }
    const intentsResult = await apiCall('/admin/intents');
    if (intentsResult.success && intentsResult.intents?.[id]) {
        showToast(__('config.intents.idExists'), 'error'); return;
    }
    const result = await apiCall(`/admin/intents/${id}`, 'POST', { enabled: true, name, description: desc });
    if (result.success) {
        showToast(__('config.intents.regSuccess'), 'success');
        document.getElementById('newIntentId').value = '';
        document.getElementById('newIntentName').value = '';
        document.getElementById('newIntentDesc').value = '';
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
        apiCall('/admin/config', 'POST', {
            settings: { 'server.language': lang }
        });
    }
}

async function saveServerConfig() {
    const selectedLang = document.getElementById('languageSelect').value || 'zh-CN';
    const serverConfig = {
        service_port: parseInt(document.getElementById('servicePort').value) || 11555,
        admin_port: parseInt(document.getElementById('adminPort').value) || 11556,
        host: document.getElementById('serverHost').value || '0.0.0.0',
        debug: true,
        language: selectedLang,
    };
    const result = await apiCall('/admin/config', 'POST', { section: 'server', values: serverConfig });
    if (result.success) {
        showToast(__('config.server.saveSuccess'), 'success');
        if (typeof i18nLoadLocale === 'function') {
            i18nLoadLocale(selectedLang);
        }
    } else {
        showToast(__('config.server.saveFailed'), 'error');
    }
}
