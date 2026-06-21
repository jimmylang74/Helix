/**
 * i18n - Client-side internationalization for AI Agent Service
 *
 * Translation engine that loads locale JSON files and translates
 * the UI via data-i18n attributes and a global __() function.
 */

let i18nLocaleData = {};
let i18nCurrentLang = 'zh-CN';

// Allow overriding the locale endpoint path from the server (set via <script> var)
let i18nLocaleUrlPrefix = '/api/admin/locale';

/**
 * Initialize i18n: load config to get current language, then load locale.
 */
async function i18nInit() {
  try {
    const resp = await apiCall('/admin/config');
    if (resp.success && resp.config && resp.config.server) {
      i18nCurrentLang = resp.config.server.language || 'zh-CN';
    }
  } catch (e) {
    console.warn('i18n: failed to load config, using default zh-CN');
  }
  await i18nLoadLocale(i18nCurrentLang);
}

/**
 * Load locale data for the given language code.
 */
async function i18nLoadLocale(lang) {
  try {
    const resp = await fetch(`${i18nLocaleUrlPrefix}/${lang}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    i18nLocaleData = await resp.json();
    i18nCurrentLang = lang;
    i18nTranslatePage();
  } catch (e) {
    console.warn(`i18n: failed to load locale "${lang}"`, e);
    // Fallback: try to load zh-CN if target lang failed
    if (lang !== 'zh-CN') {
      return i18nLoadLocale('zh-CN');
    }
  }
}

/**
 * Translate all elements with data-i18n attribute on the page.
 * Also translates document title.
 */
function i18nTranslatePage() {
  document.documentElement.lang = i18nCurrentLang;

  // Translate document title
  const titleKey = 'app.title';
  if (i18nLocaleData[titleKey]) {
    document.title = i18nLocaleData[titleKey];
  }

  // Translate elements with data-i18n attribute
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const translation = i18nLookup(key);
    if (translation !== null) {
      el.textContent = translation;
    }
  });

  // Translate input placeholders
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    const translation = i18nLookup(key);
    if (translation !== null) {
      el.placeholder = translation;
    }
  });

  // Translate input values (for submit/button inputs)
  document.querySelectorAll('[data-i18n-value]').forEach(el => {
    const key = el.getAttribute('data-i18n-value');
    const translation = i18nLookup(key);
    if (translation !== null) {
      el.value = translation;
    }
  });
}

/**
 * Look up a translation key in the current locale data.
 * Supports dot-notation: "nav.dashboard"
 * Falls back to the key itself if not found.
 * @param {string} key
 * @returns {string|null}
 */
function i18nLookup(key) {
  // Locale files use flat dot-separated keys: {"config.title": "..."}
  if (key in i18nLocaleData) {
    const v = i18nLocaleData[key];
    return typeof v === 'string' ? v : null;
  }
  // Fallback: dot-notation traversal for nested structures
  const parts = key.split('.');
  let val = i18nLocaleData;
  for (const p of parts) {
    if (val && typeof val === 'object' && p in val) {
      val = val[p];
    } else {
      return null;
    }
  }
  return typeof val === 'string' ? val : null;
}

/**
 * Global translation function.
 * Usage:
 *   const text = __('nav.dashboard');
 *   __('config.mcp.saved', {name: 'my-server'})  - variable interpolation
 *   __('config.server.saveSuccess', null, 'Fallback text') - fallback text
 * @param {string} key - Translation key
 * @param {Object|null} vars - Optional variables to interpolate {key: value}
 * @param {string|null} fallback - Fallback text if key not found
 * @returns {string}
 */
function __(key, vars, fallback) {
  let text = i18nLookup(key);
  if (text === null) {
    if (fallback !== undefined) return fallback;
    const parts = key.split('.');
    text = parts[parts.length - 1];
  }
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
    }
  }
  return text;
}

/**
 * Change the interface language.
 * Saves to server config, reloads locale, and re-translates.
 * @param {string} lang - Language code (e.g. 'zh-CN', 'en')
 */
async function setLanguage(lang) {
  if (lang === i18nCurrentLang) return;
  try {
    await apiCall('/admin/config', 'POST', {
      settings: { 'server.language': lang }
    });
  } catch (e) {
    console.warn('i18n: failed to save language preference', e);
  }
  await i18nLoadLocale(lang);
}

// Promise that resolves when i18n is fully initialized
let i18nReadyResolver;
const i18nReady = new Promise(resolve => { i18nReadyResolver = resolve; });

// Override i18nInit to signal readiness
const _origInit = i18nInit;
i18nInit = async function() {
  await _origInit();
  i18nReadyResolver();
};

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => i18nInit());
} else {
  i18nInit();
}
