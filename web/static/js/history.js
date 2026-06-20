/**
 * History Page JavaScript
 * Displays request history and generated files
 */

document.addEventListener('DOMContentLoaded', () => {
    refreshHistory();
    loadOutputFiles();
});

async function refreshHistory() {
    const result = await apiCall('/admin/history');
    const tbody = document.getElementById('historyTable');

    if (!result.success || !result.history || result.history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">暂无使用记录</td></tr>';
        return;
    }

    tbody.innerHTML = result.history.map(item => `
        <tr>
            <td><code>${item.request_id || '-'}</code></td>
            <td><span class="badge badge-info">${item.intent_type || '-'}</span></td>
            <td>${(item.user_request || '').substring(0, 60)}...</td>
            <td>
                <span class="badge ${item.success ? 'badge-success' : 'badge-danger'}">
                    ${item.success ? '成功' : '失败'}
                </span>
            </td>
            <td>${item.created_at || '-'}</td>
        </tr>
    `).join('');
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
