/**
 * Logs Viewer JavaScript
 * Displays and auto-refreshes debug logs with color coding
 */

let autoRefreshTimer = null;
let colorCodeEnabled = true;

document.addEventListener('DOMContentLoaded', () => {
    refreshLogs();
    startAutoRefresh();
});

async function refreshLogs() {
    const lines = document.getElementById('logLines').value;
    const result = await apiCall(`/admin/logs?lines=${lines}`);

    if (!result.success) {
        document.getElementById('logContainer').textContent =
            `加载日志失败: ${result.error}`;
        return;
    }

    const logContainer = document.getElementById('logContainer');
    const logs = result.logs || [];

    if (logs.length === 0) {
        logContainer.innerHTML = '<div class="log-entry info">暂无日志</div>';
        return;
    }

    if (colorCodeEnabled) {
        logContainer.innerHTML = logs.map(line => {
            const entry = document.createElement('div');
            entry.className = `log-entry ${getLogClass(line)}`;
            entry.textContent = line;
            return entry.outerHTML;
        }).join('');
    } else {
        logContainer.innerHTML = logs.map(line => {
            return `<div class="log-entry">${escapeHtml(line)}</div>`;
        }).join('');
    }

    // Auto-scroll to bottom
    logContainer.scrollTop = logContainer.scrollHeight;
}

function getLogClass(line) {
    if (line.includes('[ERROR]')) return 'error';
    if (line.includes('[WARN]')) return 'warn';
    if (line.includes('[ORCH]') || line.includes('[Orchestrator]')) return 'orchestrator';
    if (line.includes('[A→LLM]') || line.includes('[Agent→LLM]')) return 'agent_to_llm';
    if (line.includes('[LLM→A]') || line.includes('[LLM→Agent]')) return 'llm_to_agent';
    if (line.includes('[TOOL]') || line.includes('[Tool]')) return 'tool_call';
    if (line.includes('[State]')) return 'state';
    if (line.includes('[ACTION]') || line.includes('[Action]')) return 'agent_to_llm';
    if (line.includes('[LLM-Decision]') || line.includes('[DECISION]')) return 'llm_to_agent';
    return 'info';
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function toggleAutoRefresh() {
    const enabled = document.getElementById('autoRefresh').checked;
    if (enabled) {
        startAutoRefresh();
    } else {
        stopAutoRefresh();
    }
}

function startAutoRefresh() {
    stopAutoRefresh();
    autoRefreshTimer = setInterval(refreshLogs, 3000);
}

function stopAutoRefresh() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }
}

function toggleColorCode() {
    colorCodeEnabled = document.getElementById('colorCode').checked;
    refreshLogs();
}

function clearLogs() {
    document.getElementById('logContainer').innerHTML = '';
}
