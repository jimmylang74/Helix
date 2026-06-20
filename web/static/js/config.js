/**
 * Configuration Management JavaScript
 * Handles LLM, tools, intents, and server configuration
 */

document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    setupTabs();
    loadIntents();
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
    const tools = config.tools || {};
    const server = config.server || {};
    const imageSearch = tools.image_search || {};

    // LLM Provider
    document.getElementById('llmProvider').value = llm.provider || 'ollama';
    onProviderChange();

    // Ollama
    const ollama = llm.ollama || {};
    document.getElementById('ollamaBaseUrl').value = ollama.base_url || '';
    document.getElementById('ollamaModel').value = ollama.model || '';
    document.getElementById('ollamaTemp').value = ollama.temperature || 0.7;
    document.getElementById('ollamaMaxTokens').value = ollama.max_tokens || 4096;

    // OpenAI
    const openai = llm.openai || {};
    document.getElementById('openaiApiKey').value = openai.api_key || '';
    document.getElementById('openaiBaseUrl').value = openai.base_url || '';
    document.getElementById('openaiModel').value = openai.model || '';

    // Gemini
    const gemini = llm.gemini || {};
    document.getElementById('geminiApiKey').value = gemini.api_key || '';
    document.getElementById('geminiModel').value = gemini.model || '';

    // DeepSeek
    const deepseek = llm.deepseek || {};
    document.getElementById('deepseekApiKey').value = deepseek.api_key || '';
    document.getElementById('deepseekBaseUrl').value = deepseek.base_url || '';
    document.getElementById('deepseekModel').value = deepseek.model || '';

    // SearXNG
    const searxng = tools.searxng || {};
    document.getElementById('searxngEnabled').checked = searxng.enabled || false;
    document.getElementById('searxngUrl').value = searxng.base_url || '';
    document.getElementById('searxngMaxResults').value = searxng.max_results || 10;

    // Image Search
    document.getElementById('imageProvider').value = imageSearch.provider || 'pexels';
    const pexels = imageSearch.pexels || {};
    const unsplash = imageSearch.unsplash || {};
    document.getElementById('pexelsApiKey').value = pexels.api_key || '';
    document.getElementById('unsplashApiKey').value = unsplash.api_key || '';

    // Server
    document.getElementById('servicePort').value = server.service_port || 11555;
    document.getElementById('adminPort').value = server.admin_port || 11556;
    document.getElementById('serverHost').value = server.host || '0.0.0.0';
}

function onProviderChange() {
    const provider = document.getElementById('llmProvider').value;
    document.getElementById('ollamaConfig').style.display = provider === 'ollama' ? 'block' : 'none';
    document.getElementById('openaiConfig').style.display = provider === 'openai' ? 'block' : 'none';
    document.getElementById('geminiConfig').style.display = provider === 'gemini' ? 'block' : 'none';
    document.getElementById('deepseekConfig').style.display = provider === 'deepseek' ? 'block' : 'none';
}

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
            temperature: 0.7,
            max_tokens: 4096,
        },
        gemini: {
            api_key: document.getElementById('geminiApiKey').value,
            model: document.getElementById('geminiModel').value,
            temperature: 0.7,
            max_tokens: 4096,
        },
        deepseek: {
            api_key: document.getElementById('deepseekApiKey').value,
            base_url: document.getElementById('deepseekBaseUrl').value,
            model: document.getElementById('deepseekModel').value,
            temperature: 0.7,
            max_tokens: 4096,
        },
    };
}

async function saveLLMConfig() {
    const llmConfig = getLLMConfig();
    const result = await apiCall('/admin/config', 'POST', {
        section: 'llm',
        values: llmConfig,
    });
    if (result.success) {
        showToast('LLM 配置已保存', 'success');
    } else {
        showToast('保存失败: ' + (result.error || '未知错误'), 'error');
    }
}

async function testLLM() {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = '测试中...';
    const resultEl = document.getElementById('llmTestResult');

    const result = await apiCall('/admin/llm/test', 'POST');
    if (result.success) {
        resultEl.textContent = '✅ 连接成功: ' + (result.response || '').substring(0, 100);
        resultEl.style.color = 'green';
    } else {
        resultEl.textContent = '❌ 连接失败: ' + (result.error || '未知错误');
        resultEl.style.color = 'red';
    }

    btn.disabled = false;
    btn.textContent = '测试连接';
}

async function saveToolsConfig() {
    const toolsConfig = {
        searxng: {
            enabled: document.getElementById('searxngEnabled').checked,
            base_url: document.getElementById('searxngUrl').value,
            max_results: parseInt(document.getElementById('searxngMaxResults').value) || 10,
        },
        image_search: {
            provider: document.getElementById('imageProvider').value,
            pexels: { api_key: document.getElementById('pexelsApiKey').value },
            unsplash: { api_key: document.getElementById('unsplashApiKey').value },
        },
    };

    const result = await apiCall('/admin/config', 'POST', {
        section: 'tools',
        values: toolsConfig,
    });
    if (result.success) {
        showToast('工具配置已保存', 'success');
    } else {
        showToast('保存失败', 'error');
    }
}

async function saveServerConfig() {
    const serverConfig = {
        service_port: parseInt(document.getElementById('servicePort').value) || 11555,
        admin_port: parseInt(document.getElementById('adminPort').value) || 11556,
        host: document.getElementById('serverHost').value || '0.0.0.0',
        debug: true,
    };

    const result = await apiCall('/admin/config', 'POST', {
        section: 'server',
        values: serverConfig,
    });
    if (result.success) {
        showToast('服务设置已保存（部分设置需重启生效）', 'success');
    } else {
        showToast('保存失败', 'error');
    }
}

async function loadIntents() {
    const result = await apiCall('/admin/intents');
    const tbody = document.getElementById('intentsTable');

    if (!result.success || !result.intents) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center">加载失败</td></tr>';
        return;
    }

    const intents = result.intents;
    const entries = Object.entries(intents);

    if (entries.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">暂无注册的意图</td></tr>';
        return;
    }

    tbody.innerHTML = entries.map(([id, intent]) => `
        <tr>
            <td><code>${id}</code></td>
            <td>${intent.name || id}</td>
            <td>${intent.description || '-'}</td>
            <td>
                <span class="badge ${intent.enabled ? 'badge-success' : 'badge-danger'}">
                    ${intent.enabled ? '启用' : '禁用'}
                </span>
            </td>
            <td>
                <button class="btn btn-sm btn-outline" onclick="toggleIntent('${id}')">
                    ${intent.enabled ? '禁用' : '启用'}
                </button>
                <button class="btn btn-sm btn-danger" onclick="deleteIntent('${id}')">删除</button>
            </td>
        </tr>
    `).join('');
}

async function toggleIntent(intentId) {
    const result = await apiCall('/admin/config', 'POST', {
        settings: { [`intents.${intentId}.enabled`]: false },
    });

    // Get current state
    const configResult = await apiCall('/admin/config');
    if (configResult.success) {
        const current = configResult.config.intents?.[intentId]?.enabled;
        await apiCall('/admin/config', 'POST', {
            settings: { [`intents.${intentId}.enabled`]: !current },
        });
    }

    loadIntents();
    showToast('意图状态已更新', 'success');
}

async function deleteIntent(intentId) {
    if (!confirm(`确定要删除意图 "${intentId}" 吗？`)) return;

    const result = await apiCall(`/admin/intents/${intentId}`, 'DELETE');
    if (result.success) {
        loadIntents();
        showToast('意图已删除', 'success');
    } else {
        showToast('删除失败', 'error');
    }
}

async function registerIntent() {
    const id = document.getElementById('newIntentId').value.trim();
    const name = document.getElementById('newIntentName').value.trim();
    const desc = document.getElementById('newIntentDesc').value.trim();

    if (!id || !name) {
        showToast('请填写意图 ID 和名称', 'error');
        return;
    }

    const intentsResult = await apiCall('/admin/intents');
    if (intentsResult.success && intentsResult.intents?.[id]) {
        showToast('意图 ID 已存在', 'error');
        return;
    }

    const result = await apiCall(`/admin/intents/${id}`, 'POST', {
        enabled: true,
        name: name,
        description: desc,
    });

    if (result.success) {
        showToast('意图注册成功', 'success');
        document.getElementById('newIntentId').value = '';
        document.getElementById('newIntentName').value = '';
        document.getElementById('newIntentDesc').value = '';
        loadIntents();
    } else {
        showToast('注册失败', 'error');
    }
}
