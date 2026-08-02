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

// SPA navigation: intercept nav links, mount fetched pages into hidden
// .spa-page containers and toggle visibility — the quick-test page is
// never destroyed, so its DOM state and SSE connections survive navigation.
const _spaPages = new Map();   // pathname -> { container, inited }
let _spaActivePath = location.pathname;

function _spaAbsolute(path) {
    return new URL(path, location.origin).pathname;
}

function _spaLoadScripts(srcs) {
    return Promise.all(srcs.map(src => new Promise((resolve, reject) => {
        if (document.querySelector(`script[src="${src}"]`)) {
            resolve();
            return;
        }
        const s = document.createElement('script');
        s.src = src;
        s.onload = () => resolve();
        s.onerror = () => reject(new Error(`SPA: failed to load ${src}`));
        document.head.appendChild(s);
    })));
}

function _spaInitPage(path) {
    const init = {
        '/': () => initDashboardPage && initDashboardPage(),
        '/quick-test': () => initQuickTestPage && initQuickTestPage(),
        '/config': () => initConfigPage && initConfigPage(),
        '/history': () => initHistoryPage && initHistoryPage(),
    }[path];
    if (init) {
        if (typeof i18nReady !== 'undefined') {
            i18nReady.then(() => init());
        } else {
            init();
        }
    }
}

function _spaSetActiveNav(path) {
    document.querySelectorAll('.nav-link').forEach(a => {
        const href = _spaAbsolute(a.getAttribute('href') || '');
        a.classList.toggle('active', href === path);
    });
}

function _spaShow(path) {
    const entry = _spaPages.get(path);
    if (!entry) return false;
    document.querySelectorAll('.spa-page').forEach(p => { p.hidden = true; });
    entry.container.hidden = false;
    _spaActivePath = path;
    _spaSetActiveNav(path);
    if (!entry.inited) {
        entry.inited = true;
        _spaInitPage(path);
    } else if (window._spaPageRefresh && window._spaPageRefresh[path]) {
        // 已缓存的页面每次显示时刷新数据（如 /history 使用记录页）
        window._spaPageRefresh[path]();
    }
    if (typeof i18nTranslatePage === 'function') i18nTranslatePage();
    return true;
}

async function _spaFetchPage(path) {
    const resp = await fetch(path);
    if (!resp.ok) throw new Error(`SPA: HTTP ${resp.status} for ${path}`);
    const html = await resp.text();
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const main = doc.querySelector('main.main-content');
    // 提取内容时剥掉 base 模板的 #page-root 包装（fetch 的 HTML 含它），
    // 否则容器会嵌套 .spa-page 且内层 hidden 导致内容不可见
    const root = main ? main.querySelector('#page-root') : null;

    const container = document.createElement('div');
    container.className = 'spa-page';
    container.dataset.path = path;
    container.hidden = true;
    container.innerHTML = root ? root.innerHTML : (main ? main.innerHTML : doc.body.innerHTML);
    document.querySelector('.main-content').appendChild(container);

    const loaded = new Set(Array.from(document.querySelectorAll('script[src]'))
        .map(s => _spaAbsolute(s.getAttribute('src'))));
    const srcs = [];
    doc.querySelectorAll('script[src]').forEach(s => {
        const abs = _spaAbsolute(s.getAttribute('src'));
        if (!loaded.has(abs)) srcs.push(abs);
    });
    await _spaLoadScripts(srcs);
    _spaPages.set(path, { container, inited: false });
}

async function spaNavigate(path, opts = {}) {
    path = _spaAbsolute(path);
    if (path === _spaActivePath) return;
    if (!opts.fromPopstate) history.pushState({}, '', path);
    try {
        if (!_spaShow(path)) {
            await _spaFetchPage(path);
            _spaShow(path);
        }
    } catch (err) {
        console.error('SPA navigation failed, falling back to full reload:', err);
        location.href = path;
    }
}

function spaInit() {
    const root = document.getElementById('page-root');
    if (root && root.dataset.path) {
        // 初始整页加载的容器：已在 DOMContentLoaded 时完成初始化
        _spaPages.set(root.dataset.path, { container: root, inited: true });
        _spaActivePath = root.dataset.path;
        _spaSetActiveNav(root.dataset.path);
    }
    document.querySelectorAll('a.nav-link').forEach(a => {
        a.addEventListener('click', (e) => {
            const path = _spaAbsolute(a.getAttribute('href') || '');
            if (path === location.pathname) {
                e.preventDefault();
                return;
            }
            e.preventDefault();
            spaNavigate(path);
        });
    });
    window.addEventListener('popstate', () => {
        spaNavigate(location.pathname, { fromPopstate: true });
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', spaInit);
} else {
    spaInit();
}
