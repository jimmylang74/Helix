/**
 * AI Agent Service - Main JavaScript
 * Shared utilities and initialization
 */

const API_BASE = '/api';

/**
 * JSON-RPC 2.0 call helper.
 *
 * @param {string} method  – dispatch method, e.g. "config.get", "agent/router"
 * @param {object|null} params – method parameters (sent inside "params")
 * @returns {Promise<{success: boolean, ...result}|{success: false, error: string}>}
 *          Unwrapped so callers never see jsonrpc envelope internals.
 */
function _genId() {
    // crypto.randomUUID() requires secure context (HTTPS / localhost).
    // Fallback to Math.random for plain-HTTP on non-localhost.
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID().slice(0, 12);
    }
    return Math.random().toString(36).slice(2, 14);
}

async function apiCall(method, params = null) {
    const body = {
        jsonrpc: '2.0',
        id: _genId(),
        method,
    };
    if (params && typeof params === 'object') {
        body.params = params;
    }
    try {
        const response = await fetch(`${API_BASE}/rpc`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body),
        });
        const json = await response.json();

        // Unwrap JSON-RPC 2.0 envelope for callers
        if (json.jsonrpc === '2.0') {
            if (json.error) {
                return { success: false, error: json.error.message || 'RPC error' };
            }
            const result = json.result || {};
            if (typeof result === 'object' && result !== null) {
                return { success: true, ...result };
            }
            return { success: true, result };
        }

        // Fallback for non-RPC responses (locale, etc.)
        return json;
    } catch (error) {
        console.error(`RPC Error (${method}):`, error);
        return { success: false, error: error.message };
    }
}

// Utility: Toast notification
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
        position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
        padding: 12px 24px; border-radius: 8px;
        color: white; font-size: 14px; z-index: 1000;
        animation: fadeIn 0.3s ease;
        background: ${type === 'success' ? '#28a745' : type === 'error' ? '#dc3545' : '#17a2b8'};
    `;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.5s';
        setTimeout(() => toast.remove(), 500);
    }, 3000);
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    console.log('AI Agent Service Console initialized');
});
