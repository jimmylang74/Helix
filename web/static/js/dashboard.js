document.addEventListener('DOMContentLoaded', async () => {
    if (typeof i18nReady !== 'undefined') await i18nReady;
    loadSystemInfo();
});

async function loadSystemInfo() {
    const result = await apiCall('config.get');
    if (result.success) {
        const config = result.config;
        const llm = config.llm || {};
        const server = config.server || {};

        document.getElementById('llmProvider').textContent = llm.provider || '-';
        document.getElementById('llmModel').textContent = llm.model || '-';
        document.getElementById('servicePort').textContent = server.rpc_port || 11555;
        document.getElementById('adminPort').textContent = server.admin_port || 11556;
    }
}
