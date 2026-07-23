/**
 * Quick Test JavaScript
 * Handles test form submission, result display with tabs,
 * run log, todo tree, and LLM streaming logs
 *
 * State is persisted in sessionStorage so navigating away and
 * back preserves the in-progress request and form content.
 */

const STORAGE_KEY = 'qt_state';

let currentRequestId = null;
let isProcessing = false;
let llmEventSource = null;
let logEventSource = null;
let statusEventSource = null;
let logCursor = 0;
let statusCursor = 0;

function saveState() {
    const data = {
        requestId: currentRequestId,
        requestType: document.getElementById('requestType')?.value || 'auto',
        requestInput: document.getElementById('requestInput')?.value || '',
        isProcessing,
    };
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch (_) {}
}

function loadState() {
    try {
        const raw = sessionStorage.getItem(STORAGE_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch (_) {
        return null;
    }
}

function clearState() {
    try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) {}
}

document.addEventListener('DOMContentLoaded', async () => {
    if (typeof i18nReady !== 'undefined') await i18nReady;
    setupTestForm();
    startLogStream();
    restoreFromStorage();
});

async function restoreFromStorage() {
    const saved = loadState();
    if (!saved) return;

    document.getElementById('requestType').value = saved.requestType || 'auto';
    document.getElementById('requestInput').value = saved.requestInput || '';

    if (!saved.requestId) return;

    // Always query backend for actual task status instead of relying on
    // the locally-saved isProcessing flag, which may have been cleared
    // when the page was navigated away from mid-request.
    const result = await apiCall('agent/status', { request_id: saved.requestId });
    if (!result.success) {
        clearState();
        return;
    }

    currentRequestId = saved.requestId;

    if (result.final_result) {
        updateStatus('success');
        updateFinalResult(result.final_result, result.generated_files);
        saveState();
    } else {
        setProcessing(true);
        updateStatus('processing');
        clearFinalResult();
        startStatusStream(saved.requestId);
        startLlmStream(saved.requestId);
    }
}

function setupTestForm() {
    const form = document.getElementById('testForm');
    const submitBtn = document.getElementById('submitBtn');
    const btnText = document.getElementById('btnText');
    const btnSpinner = document.getElementById('btnSpinner');
    const cancelBtn = document.getElementById('cancelBtn');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const requestType = document.getElementById('requestType').value;
        const requestInput = document.getElementById('requestInput').value;

        if (!requestInput.trim()) {
            showToast(__('qt.enterRequest'), 'error');
            return;
        }

        setProcessing(true);
        updateStatus('processing');
        document.getElementById('resultIntent').textContent = '';
        const todoEl = document.getElementById('todoTreeContainer');
        if (todoEl) todoEl.innerHTML = `<div class="todo-empty">${__('qt.noTasks')}</div>`;
        clearLlmLog();
        clearFinalResult();

        try {
            await submitWithStreaming(requestType, requestInput);
        } catch (error) {
            stopStatusStream();
            setProcessing(false);
            updateStatus('error');
            showToast(`${__('qt.failure')}: ${error.message}`, 'error');
        } finally {
            saveState();
        }
    });

    cancelBtn.addEventListener('click', async () => {
        stopLlmStream();
        stopStatusStream();
        if (currentRequestId) {
            try {
                await apiCall('agent/cancel', { request_id: currentRequestId });
            } catch (_) {}
        }
        setProcessing(false);
        updateStatus('idle');
        document.getElementById('resultIntent').textContent = '';
        const todoEl2 = document.getElementById('todoTreeContainer');
        if (todoEl2) todoEl2.innerHTML = `<div class="todo-empty">${__('qt.noTasks')}</div>`;
        document.getElementById('finalResultContainer').innerHTML =
            `<span class="text-muted">${__('qt.waitingResult')}</span>`;
        document.getElementById('resultFiles').innerHTML = '';
        clearLlmLog();
        currentRequestId = null;
        clearState();
    });

    const requestInput = document.getElementById('requestInput');
    requestInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            form.requestSubmit();
        }
    });
}

async function submitWithStreaming(requestType, requestInput) {
    const rpcId = `rpc_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

    try {
        const response = await fetch('/api/rpc', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                jsonrpc: '2.0',
                id: rpcId,
                method: 'agent/router',
                params: { request: requestInput, intent: requestType },
            }),
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.error) {
            throw new Error(data.error.message || 'Request failed');
        }

        // Use the backend-assigned request_id, not the frontend rpcId
        const requestId = data.result?.request_id;
        if (requestId) {
            currentRequestId = requestId;
            logCursor = 0;
            statusCursor = 0;
            saveState();
            startLlmStream(requestId);
            startStatusStream(requestId);
        }
    } catch (error) {
        throw error;
    }
}

function startLlmStream(requestId) {
    stopLlmStream();
    const es = new EventSource(`/api/llm-stream?request_id=${encodeURIComponent(requestId)}`);
    llmEventSource = es;
    es.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);
            handleStreamEvent(event);
        } catch (_) {}
    };
    es.onerror = () => {
        es.close();
        llmEventSource = null;
    };
}

function stopLlmStream() {
    if (llmEventSource) {
        llmEventSource.close();
        llmEventSource = null;
    }
}

function handleStreamEvent(event) {
    switch (event.type) {
        case 'thinking_delta':
        case 'thinking':
            addLlmLogEntry({ type: 'thinking', content: event.delta || event.content || '' });
            break;
        case 'thinking_end':
            addLlmLogEntry({ type: 'thinking_end' });
            break;
        case 'assistant_delta':
        case 'assistant':
            addLlmLogEntry({ type: 'assistant', content: event.delta || event.content || '' });
            break;
        case 'assistant_end':
            addLlmLogEntry({ type: 'assistant_end' });
            break;
        case 'tool_call_begin':
        case 'tool_call_start':
            addLlmLogEntry({ type: 'tool_call', name: event.name || '', id: event.id || '' });
            break;
        case 'tool_call_delta':
            addLlmLogEntry({ type: 'tool_call_delta', id: event.id || '', content: event.delta || event.arguments || '' });
            break;
        case 'tool_call_end':
        case 'tool_call_complete':
            addLlmLogEntry({ type: 'tool_call_end', id: event.id || '', arguments: event.arguments || '' });
            break;
        case 'tool_call_result':
            addLlmLogEntry({ type: 'tool_call_result', id: event.id || '', name: event.name || '', result: event.result || '' });
            break;
        case 'usage':
            addLlmLogEntry({
                type: 'usage',
                prompt_tokens: event.prompt_tokens || 0,
                completion_tokens: event.completion_tokens || 0,
                reasoning_tokens: event.reasoning_tokens || 0,
            });
            break;
        case 'done':
        case 'complete':
            addLlmLogEntry({ type: 'done', finish_reason: event.finish_reason || '' });
            updateStatus('success');
            break;
        case 'error':
            addLlmLogEntry({ type: 'error', message: event.message || event.error || 'Unknown error' });
            updateStatus('error');
            break;
        case 'sending':
            addLlmLogEntry({
                type: 'sending',
                provider: event.provider || '',
                model: event.model || '',
                system_prompt: event.system_prompt || '',
                user_message: event.user_message || '',
            });
            break;
    }
}

function updateStatus(status) {
    const el = document.getElementById('resultStatus');
    switch (status) {
        case 'processing':
            el.textContent = __('qt.processing');
            el.className = 'badge badge-info';
            break;
        case 'success':
            el.textContent = __('qt.success');
            el.className = 'badge badge-success';
            break;
        case 'error':
            el.textContent = __('qt.error');
            el.className = 'badge badge-danger';
            break;
        case 'idle':
            el.textContent = __('qt.idle');
            el.className = 'badge badge-secondary';
            break;
    }
}

function setProcessing(processing) {
    isProcessing = processing;
    const submitBtn = document.getElementById('submitBtn');
    const btnText = document.getElementById('btnText');
    const btnSpinner = document.getElementById('btnSpinner');
    const cancelBtn = document.getElementById('cancelBtn');

    if (processing) {
        btnText.textContent = __('qt.processing');
        btnSpinner.classList.remove('hidden');
        submitBtn.disabled = true;
        cancelBtn.style.display = 'inline-flex';
    } else {
        btnText.textContent = __('qt.sendRequest');
        btnSpinner.classList.add('hidden');
        submitBtn.disabled = false;
        cancelBtn.style.display = 'none';
    }
}

// ========== Run Log (SSE) ==========

function startLogStream() {
    stopLogStream();
    const url = logCursor > 0
        ? `/api/log-stream?cursor=${logCursor}`
        : '/api/log-stream';
    const es = new EventSource(url);
    logEventSource = es;
    es.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);
            if (event.type === 'log' && event.lines) {
                if (event.cursor) logCursor = event.cursor;
                appendLogLines(event.lines);
            }
        } catch (_) {}
    };
    es.onerror = () => {
        es.close();
        logEventSource = null;
    };
}

function stopLogStream() {
    if (logEventSource) {
        logEventSource.close();
        logEventSource = null;
    }
}

function restartLogStream() {
    stopLogStream();
    startLogStream();
}

function appendLogLines(lines) {
    const container = document.getElementById('runLogContainer');
    if (!container) return;

    if (container.querySelector('.log-entry.info') && container.children.length === 1) {
        container.innerHTML = '';
    }

    lines.forEach(line => {
        const div = document.createElement('div');
        div.className = `log-entry ${getLogClass(line)}`;
        div.textContent = line;
        container.appendChild(div);
    });

    const autoScroll = document.getElementById('autoRefreshLog')?.checked !== false;
    if (autoScroll) container.scrollTop = container.scrollHeight;
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

function toggleAutoRefreshLog() {
    // kept for HTML compatibility; log stream is always active
}

// ========== Status Stream (SSE) ==========

function startStatusStream(requestId) {
    stopStatusStream();
    const cursorParam = statusCursor > 0 ? `&cursor=${statusCursor}` : '';
    const es = new EventSource(`/api/status-stream?request_id=${encodeURIComponent(requestId)}${cursorParam}`);
    statusEventSource = es;
    es.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);
            if (event.type === 'status') {
                if (event.cursor) statusCursor = event.cursor;
                handleStatusEvent(event);
            }
        } catch (_) {}
    };
    es.onerror = () => {
        es.close();
        statusEventSource = null;
    };
}

function stopStatusStream() {
    if (statusEventSource) {
        statusEventSource.close();
        statusEventSource = null;
    }
}

function handleStatusEvent(state) {
    renderTodoTree(state);

    if (state.final_result) {
        updateFinalResult(state.final_result, state.generated_files);
        stopStatusStream();
        setProcessing(false);
        updateStatus('success');
        saveState();
    } else if (state.error) {
        stopStatusStream();
        setProcessing(false);
        updateStatus('error');
        saveState();
    }
}

function renderTodoTree(state) {
    const container = document.getElementById('todoTreeContainer');
    if (!container) return;
    const todoList = state.todo_list || [];
    const currentIdx = state.current_todo_idx || 0;
    const todoSubtaskLists = state.todo_subtask_lists || [];
    const subtaskStatus = state.subtask_status || 'idle';

    if (todoList.length === 0) {
        container.innerHTML = `<div class="todo-empty">${__('qt.noTasks')}</div>`;
        return;
    }

    let html = '<ul class="todo-list">';
    todoList.forEach((todo, idx) => {
        let statusClass = 'todo-pending';
        let icon = '⬜';
        if (idx < currentIdx) {
            statusClass = 'todo-completed';
            icon = '✓';
        } else if (idx === currentIdx && subtaskStatus !== 'completed') {
            statusClass = 'todo-running';
            icon = '<span class="spinner-inline"></span>';
        }
        html += `<li class="todo-item ${statusClass}">
            <div class="todo-header"><span class="todo-icon">${icon}</span><span class="todo-text">${escapeHtml(todo)}</span></div>`;
        const subtasks = todoSubtaskLists[idx] || [];
        if (subtasks.length > 0) {
            html += '<ul class="subtask-list">';
            subtasks.forEach((st) => {
                let sIcon = '⬜';
                if (st.status === 'completed') sIcon = '✓';
                else if (st.status === 'failed') sIcon = '✗';
                else if (st.status === 'running') sIcon = '<span class="spinner-inline"></span>';
                html += `<li class="subtask-item">
                    <div class="subtask-header">${sIcon} ${escapeHtml(st.subtask || '')}</div>
                </li>`;
            });
            html += '</ul>';
        }
        html += '</li>';
    });
    html += '</ul>';
    container.innerHTML = html;
}

// ========== Final Result ==========

function clearFinalResult() {
    document.getElementById('finalResultContainer').innerHTML =
        `<span class="text-muted">${__('qt.processingResult')}</span>`;
    document.getElementById('resultFiles').innerHTML = '';
}

function updateFinalResult(content, files) {
    if (content) {
        document.getElementById('finalResultContainer').textContent = content;
    }
    if (files && files.length > 0) {
        const filesDiv = document.getElementById('resultFiles');
        if (filesDiv.children.length > 0) return;
        const title = document.createElement('p');
        title.textContent = __('qt.generatedFiles');
        title.style.fontWeight = 'bold';
        title.style.marginBottom = '8px';
        filesDiv.appendChild(title);
        files.forEach(file => {
            const link = document.createElement('a');
            link.href = `/output/${file.split('/').pop()}`;
            link.textContent = file.split('/').pop();
            link.target = '_blank';
            filesDiv.appendChild(link);
        });
    }
}

// ========== LLM Log ==========

let _lastLlmLogType = null;
let _lastLlmLogDiv = null;

function addLlmLogEntry(entry) {
    const container = document.getElementById('llmLogContainer');
    const autoScroll = document.getElementById('llmAutoScroll').checked;

    const isDelta = entry.type === 'thinking' || entry.type === 'assistant' || entry.type === 'tool_call_delta';
    const canAppend = isDelta && _lastLlmLogType === entry.type && _lastLlmLogDiv;

    if (canAppend) {
        const textNode = document.createTextNode(entry.content || entry.delta || '');
        _lastLlmLogDiv.appendChild(textNode);
    } else {
        const div = document.createElement('div');
        div.className = `llm-log-entry llm-log-${entry.type}`;

        switch (entry.type) {
            case 'sending':
                div.innerHTML = `<span class="llm-log-sending-label">📤 Sending to LLM:</span> <span class="llm-log-sending-model">${escapeHtml(entry.provider)}/${escapeHtml(entry.model)}</span>`;
                if (entry.system_prompt) {
                    const spDiv = document.createElement('div');
                    spDiv.className = 'llm-log-sending-detail';
                    spDiv.innerHTML = `<span class="llm-log-sending-detail-label">System:</span> ${escapeHtml(entry.system_prompt)}`;
                    div.appendChild(spDiv);
                }
                if (entry.user_message) {
                    const umDiv = document.createElement('div');
                    umDiv.className = 'llm-log-sending-detail';
                    umDiv.innerHTML = `<span class="llm-log-sending-detail-label">User:</span> ${escapeHtml(entry.user_message)}`;
                    div.appendChild(umDiv);
                }
                break;
            case 'thinking':
                div.innerHTML = `<span class="llm-log-thinking-label">💭 Thinking:</span> ${escapeHtml(entry.content)}`;
                break;
            case 'thinking_end':
                div.innerHTML = '<span class="llm-log-thinking-label">💭 [end]</span>';
                break;
            case 'assistant':
                div.innerHTML = `<span class="llm-log-assistant-label">🤖 Assistant:</span> ${escapeHtml(entry.content)}`;
                break;
            case 'assistant_end':
                div.innerHTML = '<span class="llm-log-assistant-label">🤖 [end]</span>';
                break;
            case 'tool_call':
                div.innerHTML = `<span class="llm-log-tool-label">🔧 Tool Call:</span> <strong>${escapeHtml(entry.name)}</strong> (${entry.id})`;
                break;
            case 'tool_call_delta':
                div.innerHTML = `<span class="llm-log-tool-label">🔧 Tool Args:</span> ${escapeHtml(entry.content)}`;
                break;
            case 'tool_call_end':
                div.innerHTML = `<span class="llm-log-tool-label">🔧 Tool Complete:</span> ${entry.id}`;
                break;
            case 'tool_call_result':
                div.innerHTML = `<span class="llm-log-tool-result-label">📋 Toolcall Result [${escapeHtml(entry.name)}]:</span> ${escapeHtml(entry.result)}`;
                break;
            case 'usage':
                div.innerHTML = `<span class="llm-log-usage-label">📊 Usage:</span> prompt=${entry.prompt_tokens}, completion=${entry.completion_tokens}, reasoning=${entry.reasoning_tokens}`;
                break;
            case 'done':
                div.innerHTML = `<span class="llm-log-done-label">✅ Done:</span> ${entry.finish_reason || 'completed'}`;
                break;
            case 'error':
                div.innerHTML = `<span class="llm-log-error-label">❌ Error:</span> ${escapeHtml(entry.message)}`;
                break;
            default:
                div.innerHTML = `<span class="llm-log-unknown-label">[${entry.type}]</span> ${escapeHtml(JSON.stringify(entry))}`;
        }

        container.appendChild(div);
        _lastLlmLogType = isDelta ? entry.type : null;
        _lastLlmLogDiv = isDelta ? div : null;
    }

    if (autoScroll) container.scrollTop = container.scrollHeight;
}

function clearLlmLog() {
    document.getElementById('llmLogContainer').innerHTML = '';
    _lastLlmLogType = null;
    _lastLlmLogDiv = null;
}

function exportLlmLog() {
    const container = document.getElementById('llmLogContainer');
    if (!container.textContent.trim()) {
        showToast(__('qt.logEmpty') || '日志为空', 'warning');
        return;
    }

    const lines = [];
    container.querySelectorAll('.llm-log-entry').forEach(el => {
        lines.push(el.textContent.trim());
    });
    const content = lines.join('\n');
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const filename = `llm_log_${timestamp}.txt`;

    if (window.showDirectoryPicker) {
        window.showDirectoryPicker().then(dirHandle => {
            dirHandle.getFileHandle(filename, { create: true }).then(fileHandle => {
                fileHandle.createWritable().then(writable => {
                    writable.write(content).then(() => {
                        writable.close();
                        showToast(__('qt.exportSuccess') || '导出成功', 'success');
                    });
                });
            });
        }).catch(() => {});
    } else {
        const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
        showToast(__('qt.exportSuccess') || '导出成功', 'success');
    }
}

function exportRunLog() {
    const container = document.getElementById('runLogContainer');
    if (!container.textContent.trim()) {
        showToast(__('qt.logEmpty') || '日志为空', 'warning');
        return;
    }
    const content = container.textContent;
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const filename = `run_log_${timestamp}.txt`;

    if (window.showDirectoryPicker) {
        window.showDirectoryPicker().then(dirHandle => {
            dirHandle.getFileHandle(filename, { create: true }).then(fileHandle => {
                fileHandle.createWritable().then(writable => {
                    writable.write(content).then(() => {
                        writable.close();
                        showToast(__('qt.exportSuccess') || '导出成功', 'success');
                    });
                });
            });
        }).catch(() => {});
    } else {
        const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
        showToast(__('qt.exportSuccess') || '导出成功', 'success');
    }
}

// ========== Copy Result ==========

document.getElementById('copyBtn')?.addEventListener('click', () => {
    const container = document.getElementById('finalResultContainer');
    if (container.textContent) {
        navigator.clipboard.writeText(container.textContent)
            .then(() => showToast(__('qt.copySuccess'), 'success'))
            .catch(() => showToast(__('qt.copyError'), 'error'));
    }
});

// ========== Utilities ==========

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
        position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
        padding: 12px 24px; border-radius: 8px;
        color: white; font-size: 14px; z-index: 1000;
        background: ${type === 'success' ? '#28a745' : type === 'error' ? '#dc3545' : type === 'warning' ? '#ffc107' : '#17a2b8'};
    `;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.5s';
        setTimeout(() => toast.remove(), 500);
    }, 3000);
}
