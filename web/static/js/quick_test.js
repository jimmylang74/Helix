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

function initQuickTestPage() {
    if (window.__qtInited) return;
    window.__qtInited = true;
    setupTestForm();
    setupLogTabs();
    startLogStream();
    restoreFromStorage();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', async () => {
        if (typeof i18nReady !== 'undefined') await i18nReady;
        initQuickTestPage();
    });
} else {
    initQuickTestPage();
}

function setupLogTabs() {
    const panel = document.getElementById('logPanel');
    if (!panel) return;
    const showControls = (tab) => {
        const runControls = document.getElementById('controls-runlog');
        const llmControls = document.getElementById('controls-llmlog');
        if (runControls) runControls.style.display = tab === 'runlog' ? 'flex' : 'none';
        if (llmControls) llmControls.style.display = tab === 'llmlog' ? 'flex' : 'none';
    };
    panel.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            panel.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            panel.querySelectorAll('.qt-tab-pane').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            const pane = document.getElementById(`pane-${btn.dataset.tab}`);
            if (pane) pane.classList.add('active');
            showControls(btn.dataset.tab);
        });
    });
    showControls('runlog');
}

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
        updateTokenUsage(result.token_usage);
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
        const todoEl = document.getElementById('todoTreeContainer');
        if (todoEl) todoEl.innerHTML = `<div class="todo-empty">${__('qt.noTasks')}</div>`;
        clearLlmLog();
        clearFinalResult();
        resetTokenUsage();

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
        const todoEl2 = document.getElementById('todoTreeContainer');
        if (todoEl2) todoEl2.innerHTML = `<div class="todo-empty">${__('qt.noTasks')}</div>`;
        clearFinalResult(__('qt.waitingResult'));
        clearLlmLog();
        resetTokenUsage();
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
            // ask_user 已回答完成（含状态重放场景）：收起输入表单，保留问题与回答展示。
            // 事件按发出顺序到达（先 tool_call_result 后新一轮 ask_user），不会误伤新表单
            if (event.name === 'ask_user') {
                const input = document.getElementById('askUserInput');
                if (input) {
                    const block = input.closest('.ask-user-block');
                    if (block) {
                        const form = block.querySelector('.ask-user-form');
                        if (form) form.remove();
                        block.classList.remove('ask-user-block');
                    }
                    updateStatus('processing');
                }
            }
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
        case 'ask_user':
            showAskUserQuestion(event.question || '');
            break;
    }
}

function updateStatus(status) {
    const el = document.getElementById('resultStatus');
    if (!el) return;
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
        case 'awaiting':
            el.textContent = __('qt.awaitingAnswer');
            el.className = 'badge badge-warning';
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
    updateTokenUsage(state.token_usage);
    renderTaskGraph(state);

    // 节点完成时，将节点执行结果实时追加到最终结果窗口（累积式，后续内容不覆盖先到的结果）
    if (state.node_result && state.node_result.response) {
        appendNodeResult(state.node_result);
    }

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

function renderTaskGraph(state) {
    const container = document.getElementById('todoTreeContainer');
    if (!container) return;

    const phase = state.orchestrator_phase || '';
    const nodes = state.task_graph_nodes || [];

    if (phase === 'planning' && nodes.length === 0) {
        container.innerHTML = `<div class="todo-empty">${__('qt.planning')}</div>`;
        return;
    }
    if (nodes.length === 0) {
        container.innerHTML = `<div class="todo-empty">${__('qt.noTasks')}</div>`;
        return;
    }

    const stateLabels = {
        'Pending': __('qt.statePending'),
        'Ready': __('qt.stateReady'),
        'Running': __('qt.stateRunning'),
        'Done': __('qt.stateDone'),
        'Failed': __('qt.stateFailed'),
    };

    let html = '<ul class="todo-list">';
    nodes.forEach((node) => {
        const nodeState = node.state || 'Pending';
        let statusClass = 'todo-pending';
        let icon = '⬜';
        if (nodeState === 'Done') {
            statusClass = 'todo-completed';
            icon = '✓';
        } else if (nodeState === 'Failed') {
            statusClass = 'todo-failed';
            icon = '✗';
        } else if (nodeState === 'Running') {
            statusClass = 'todo-running';
            icon = '<span class="spinner-inline"></span>';
        } else if (nodeState === 'Ready') {
            statusClass = 'todo-ready';
            icon = '▶';
        }

        const label = stateLabels[nodeState] || nodeState;
        html += `<li class="todo-item ${statusClass}">
            <div class="todo-header">
                <span class="todo-icon">${icon}</span>
                <span class="todo-text">${escapeHtml(node.title || node.id)}</span>
                <span class="todo-badge">${label}</span>
            </div>`;

        const deps = node.depends || [];
        if (deps.length > 0) {
            html += `<div class="todo-deps">${__('qt.dependsOn')}: ${escapeHtml(deps.join(', '))}</div>`;
        }

        if (node.response) {
            html += `<div class="todo-response">${escapeHtml(node.response.substring(0, 120))}</div>`;
        }

        html += '</li>';
    });
    html += '</ul>';

    const doneCount = nodes.filter(n => n.state === 'Done').length;
    const failedCount = nodes.filter(n => n.state === 'Failed').length;
    const totalCount = nodes.length;
    html += `<div class="todo-summary">${doneCount}/${totalCount} ${__('qt.nodesComplete')}`;
    if (failedCount > 0) {
        html += `, ${failedCount} ${__('qt.nodesFailed')}`;
    }
    html += '</div>';

    container.innerHTML = html;
}

// ========== Token Usage ==========

function updateTokenUsage(tokenUsage) {
    if (!tokenUsage) return;
    const inEl = document.getElementById('tokenInput');
    const outEl = document.getElementById('tokenOutput');
    const tzEl = document.getElementById('tokenizerName');
    if (inEl) {
        inEl.textContent = `${tokenUsage.input_tokens ?? 0}/${tokenUsage.total_input_tokens ?? 0}`;
    }
    if (outEl) {
        outEl.textContent = `${tokenUsage.output_tokens ?? 0}/${tokenUsage.total_output_tokens ?? 0}`;
    }
    if (tzEl) {
        tzEl.textContent = tokenUsage.tokenizer || '-';
    }
}

function resetTokenUsage() {
    const inEl = document.getElementById('tokenInput');
    const outEl = document.getElementById('tokenOutput');
    const tzEl = document.getElementById('tokenizerName');
    if (inEl) inEl.textContent = '0/0';
    if (outEl) outEl.textContent = '0/0';
    if (tzEl) tzEl.textContent = '-';
}

// ========== Final Result ==========

// 累积式结果窗口：每个节点结果 / 最终结果作为独立块追加显示，
// 后到的内容不会覆盖先到的内容。去重状态随请求生命周期重置。
let _displayedNodeResultIds = new Set();
let _finalResultShown = false;

function clearFinalResult(placeholder) {
    _displayedNodeResultIds = new Set();
    _finalResultShown = false;
    document.getElementById('finalResultContainer').innerHTML =
        `<span class="text-muted">${placeholder || __('qt.processingResult')}</span>`;
    document.getElementById('resultFiles').innerHTML = '';
}

function clearPlaceholder(container) {
    const only = container.children.length === 1 ? container.firstElementChild : null;
    if (only && only.classList.contains('text-muted')) {
        container.innerHTML = '';
    }
}

function appendNodeResult(nodeResult) {
    const container = document.getElementById('finalResultContainer');
    if (!container || !nodeResult || !nodeResult.response) return;

    const nodeId = nodeResult.node_id || nodeResult.node_title || '';
    if (nodeId) {
        if (_displayedNodeResultIds.has(nodeId)) return;
        _displayedNodeResultIds.add(nodeId);
    }

    clearPlaceholder(container);

    const block = document.createElement('div');
    block.className = 'result-block result-block-node';
    const header = document.createElement('div');
    header.className = 'result-block-header';
    header.textContent = `▶ ${__('qt.nodeResult')}${nodeResult.node_title ? ': ' + nodeResult.node_title : ''}`;
    const content = document.createElement('div');
    content.className = 'result-block-content';
    content.textContent = nodeResult.response;
    block.appendChild(header);
    block.appendChild(content);
    container.appendChild(block);
    container.scrollTop = container.scrollHeight;
}

function appendFinalResult(content, files) {
    if (content) {
        if (_finalResultShown) return;
        const container = document.getElementById('finalResultContainer');
        if (!container) return;
        _finalResultShown = true;
        clearPlaceholder(container);

        const block = document.createElement('div');
        block.className = 'result-block result-block-final';
        const header = document.createElement('div');
        header.className = 'result-block-header';
        header.textContent = `✅ ${__('qt.finalResult')}`;
        const contentDiv = document.createElement('div');
        contentDiv.className = 'result-block-content';
        contentDiv.textContent = content;
        block.appendChild(header);
        block.appendChild(contentDiv);
        container.appendChild(block);
        container.scrollTop = container.scrollHeight;
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

function updateFinalResult(content, files) {
    appendFinalResult(content, files);
}

// ========== Ask User (ask_user tool) ==========

function showAskUserQuestion(question) {
    const container = document.getElementById('finalResultContainer');
    if (!container) return;

    clearPlaceholder(container);
    // 删除全部旧块，防止残留输入框被 getElementById('askUserInput') 命中旧值
    container.querySelectorAll('.ask-user-block').forEach(b => b.remove());

    const block = document.createElement('div');
    // 同时挂 ask-user-block 类，供 submitAskUserAnswer / tool_call_result / 旧块清理三处移除逻辑命中
    block.className = 'result-block result-block-ask ask-user-block';

    const header = document.createElement('div');
    header.className = 'result-block-header';
    header.textContent = `❓ ${__('qt.askUserTitle')}`;
    block.appendChild(header);

    const content = document.createElement('div');
    content.className = 'result-block-content';
    content.textContent = question || '';
    block.appendChild(content);

    const form = document.createElement('div');
    form.className = 'ask-user-form';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control form-control-sm';
    input.id = 'askUserInput';
    input.placeholder = __('qt.answerPlaceholder');
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            submitAskUserAnswer(input);
        }
    });

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-primary btn-sm';
    btn.textContent = __('qt.submitAnswer');
    btn.addEventListener('click', () => submitAskUserAnswer(input));

    form.appendChild(input);
    form.appendChild(btn);
    block.appendChild(form);

    container.appendChild(block);
    container.scrollTop = container.scrollHeight;
    updateStatus('awaiting');
    input.focus();
}

async function submitAskUserAnswer(input) {
    const btn = input ? input.nextElementSibling : null;
    if (!input || !currentRequestId) return;

    const answer = input.value.trim();
    if (!answer) {
        showToast(__('qt.enterAnswer'), 'warning');
        return;
    }

    if (btn) btn.disabled = true;
    try {
        const result = await apiCall('agent/router', {
            request_id: currentRequestId,
            answer: answer,
        });
        if (!result.success) {
            showToast(result.error || __('qt.failure'), 'error');
            if (btn) btn.disabled = false;
            return;
        }
        // 回答已投递到后端：把回答展示到最终结果窗口，并收起提问块的输入表单
        appendUserAnswer(answer);
        const block = input.closest('.ask-user-block');
        if (block) {
            const form = block.querySelector('.ask-user-form');
            if (form) form.remove();
            // 摘掉 ask-user-block，问答对常驻最终结果窗口，不再被后续新问题清理
            block.classList.remove('ask-user-block');
        }
        updateStatus('processing');
    } catch (error) {
        if (btn) btn.disabled = false;
        showToast(error.message || __('qt.failure'), 'error');
    }
}

function appendUserAnswer(answer) {
    const container = document.getElementById('finalResultContainer');
    if (!container || !answer) return;

    const block = document.createElement('div');
    block.className = 'result-block result-block-ask';
    const header = document.createElement('div');
    header.className = 'result-block-header';
    header.textContent = `💬 ${__('qt.userAnswer')}`;
    const content = document.createElement('div');
    content.className = 'result-block-content';
    content.textContent = answer;
    block.appendChild(header);
    block.appendChild(content);
    container.appendChild(block);
    container.scrollTop = container.scrollHeight;
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
                div.innerHTML = `<span class="llm-log-sending-label">📤 Sending to LLM:</span> <span class="llm-log-sending-model">${escapeHtml(entry.provider)}/${escapeHtml(entry.model)}</span><br>`;
                if (entry.system_prompt) {
                    const spDiv = document.createElement('div');
                    spDiv.className = 'llm-log-sending-detail';
                    spDiv.innerHTML = `<span class="llm-log-sending-detail-label">System:</span><br>${escapeHtml(entry.system_prompt)}`;
                    div.appendChild(spDiv);
                }
                if (entry.user_message) {
                    const umDiv = document.createElement('div');
                    umDiv.className = 'llm-log-sending-detail';
                    umDiv.innerHTML = `<span class="llm-log-sending-detail-label">User:</span><br>${escapeHtml(entry.user_message)}`;
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
        lines.push(el.innerText.trim());
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
