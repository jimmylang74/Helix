/**
 * Dashboard JavaScript
 * Handles test form submission, result display, and system info loading
 */

document.addEventListener('DOMContentLoaded', () => {
    loadSystemInfo();
    setupTestForm();
});

async function loadSystemInfo() {
    const result = await apiCall('/admin/config');
    if (result.success) {
        const config = result.config;
        const llm = config.llm || {};
        const provider = llm.provider || 'ollama';
        const providerConfig = llm[provider] || {};
        const server = config.server || {};

        document.getElementById('llmProvider').textContent = provider;
        document.getElementById('llmModel').textContent = providerConfig.model || '-';
        document.getElementById('servicePort').textContent = server.service_port || 11555;
        document.getElementById('adminPort').textContent = server.admin_port || 11556;
    }
}

function setupTestForm() {
    const form = document.getElementById('testForm');
    const submitBtn = document.getElementById('submitBtn');
    const btnText = document.getElementById('btnText');
    const btnSpinner = document.getElementById('btnSpinner');
    const resultCard = document.getElementById('resultCard');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const requestType = document.getElementById('requestType').value;
        const requestInput = document.getElementById('requestInput').value;

        if (!requestInput.trim()) {
            showToast('请输入请求内容', 'error');
            return;
        }

        // Show loading state
        btnText.textContent = '处理中...';
        btnSpinner.classList.remove('hidden');
        submitBtn.disabled = true;
        resultCard.style.display = 'block';
        document.getElementById('resultContent').textContent = '等待响应...';
        document.getElementById('resultStatus').textContent = '处理中...';
        document.getElementById('resultStatus').className = 'badge badge-info';

        try {
            const result = await apiCall('/agent/router', 'POST', {
                request: requestInput,
                intent: requestType,
                stream: false,
            });

            // Update result UI
            const statusEl = document.getElementById('resultStatus');
            if (result.success) {
                statusEl.textContent = '✅ 成功';
                statusEl.className = 'badge badge-success';
            } else {
                statusEl.textContent = '❌ 失败';
                statusEl.className = 'badge badge-danger';
            }

            document.getElementById('resultIntent').textContent =
                `类型: ${result.intent_type || '-'}`;
            document.getElementById('resultId').textContent =
                `ID: ${result.request_id || '-'}`;

            const resultContent = document.getElementById('resultContent');
            if (result.final_result) {
                resultContent.textContent = result.final_result;
            } else if (result.error) {
                resultContent.textContent = `错误: ${result.error}`;
            } else {
                resultContent.textContent = JSON.stringify(result, null, 2);
            }

            // Show generated files
            const filesDiv = document.getElementById('resultFiles');
            filesDiv.innerHTML = '';
            if (result.generated_files && result.generated_files.length > 0) {
                const title = document.createElement('p');
                title.textContent = '📄 生成的文件:';
                title.style.fontWeight = 'bold';
                title.style.marginBottom = '8px';
                filesDiv.appendChild(title);
                result.generated_files.forEach(file => {
                    const link = document.createElement('a');
                    link.href = `/output/${file.split('/').pop()}`;
                    link.textContent = file.split('/').pop();
                    link.target = '_blank';
                    filesDiv.appendChild(link);
                });
            }

        } catch (error) {
            document.getElementById('resultContent').textContent =
                `请求失败: ${error.message}`;
            document.getElementById('resultStatus').textContent = '❌ 错误';
            document.getElementById('resultStatus').className = 'badge badge-danger';
        } finally {
            btnText.textContent = '发送请求';
            btnSpinner.classList.add('hidden');
            submitBtn.disabled = false;
        }
    });
}

function copyResult() {
    const content = document.getElementById('resultContent');
    if (content.textContent) {
        navigator.clipboard.writeText(content.textContent)
            .then(() => showToast('已复制到剪贴板', 'success'))
            .catch(() => showToast('复制失败', 'error'));
    }
}
