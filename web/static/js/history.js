/**
 * History Page JavaScript
 * Displays request history and generated files
 */

function initHistoryPage() {
    if (window.__historyInited) return;
    window.__historyInited = true;
    refreshHistory();
    loadOutputFiles();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', async () => {
        if (typeof i18nReady !== 'undefined') await i18nReady;
        initHistoryPage();
    });
} else {
    initHistoryPage();
}

// SPA 每次显示本页时重新拉取数据，避免展示缓存的旧记录
window._spaPageRefresh = window._spaPageRefresh || {};
window._spaPageRefresh['/history'] = refreshHistory;

async function refreshHistory() {
    const result = await apiCall('history.get');
    const tbody = document.getElementById('historyTable');

    if (!result.success || !result.history || result.history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">' + __('history.noRecords') + '</td></tr>';
        return;
    }

    tbody.innerHTML = result.history.map((item, idx) => `
        <tr class="history-table-row" onclick="showHistoryDetail(${idx})" style="cursor:pointer;">
            <td><code>${item.request_id || '-'}</code></td>
            <td><span class="badge badge-info">${item.intent_type || '-'}</span></td>
            <td>${escapeHtml((item.user_request || '').substring(0, 60))}${(item.user_request || '').length > 60 ? '...' : ''}</td>
            <td>
                <span class="badge ${item.success ? 'badge-success' : 'badge-danger'}">
                    ${item.success ? __('common.success') : __('common.failed')}
                </span>
            </td>
            <td>${item.created_at || '-'}</td>
        </tr>
    `).join('');
    window._historyRecords = result.history;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

function showHistoryDetail(idx) {
    const records = window._historyRecords || [];
    const item = records[idx];
    if (!item) return;

    document.getElementById('detailRequestId').textContent = item.request_id || '-';
    document.getElementById('detailIntentType').textContent = item.intent_type || '-';
    document.getElementById('detailCreatedAt').textContent = item.created_at || '-';
    document.getElementById('detailStatus').innerHTML = item.success
        ? '<span class="badge badge-success">' + __('common.success') + '</span>'
        : '<span class="badge badge-danger">' + __('common.failed') + '</span>';
    document.getElementById('detailUserRequest').textContent = item.user_request || '-';
    document.getElementById('detailFinalAnswer').textContent = item.final_answer || '-';

    const errorGroup = document.getElementById('detailErrorGroup');
    if (item.error) {
        errorGroup.style.display = '';
        document.getElementById('detailError').textContent = item.error;
    } else {
        errorGroup.style.display = 'none';
    }

    const filesGroup = document.getElementById('detailFilesGroup');
    const files = item.generated_files || [];
    if (files.length > 0) {
        filesGroup.style.display = '';
        document.getElementById('detailFiles').innerHTML = files.map(f =>
            '<a href="/output/' + encodeURIComponent(f) + '" target="_blank">' + escapeHtml(f) + '</a>'
        ).join(', ');
    } else {
        filesGroup.style.display = 'none';
    }

    document.getElementById('historyDetailModal').style.display = 'flex';
}

function closeHistoryDetail() {
    document.getElementById('historyDetailModal').style.display = 'none';
}

async function loadOutputFiles() {
    // List output directory
    try {
        const response = await fetch('/output/');
        // This may not work directly - depends on Flask config
        // Fallback: show message
    } catch (e) {
        // Directory listing may not be available
    }
}
