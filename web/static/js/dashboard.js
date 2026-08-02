function initDashboardPage() {
    if (window.__dashboardInited) return;
    window.__dashboardInited = true;
    loadSystemInfo();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', async () => {
        if (typeof i18nReady !== 'undefined') await i18nReady;
        initDashboardPage();
    });
} else {
    initDashboardPage();
}

async function loadSystemInfo() {
    const result = await apiCall('config.get');
    if (result.success) {
        const config = result.config;
        const llm = config.llm || {};
        const server = config.server || {};

        document.getElementById('dashLlmProvider').textContent = llm.provider || '-';
        document.getElementById('dashLlmModel').textContent = llm.model || '-';
        document.getElementById('dashServicePort').textContent = server.rpc_port || 11555;
        document.getElementById('dashAdminPort').textContent = server.admin_port || 11556;
    }
}
