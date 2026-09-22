"use strict";
/**
 * Grammar Loading and Caching
 *
 * Uses web-tree-sitter (WASM) for universal cross-platform support.
 * Grammars are loaded lazily — only languages actually present in the project
 * are compiled, keeping V8 WASM memory pressure low on large codebases.
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.EXTENSION_MAP = void 0;
exports.isSourceFile = isSourceFile;
exports.isShopifyLiquidJson = isShopifyLiquidJson;
exports.isPlayRoutesFile = isPlayRoutesFile;
exports.initGrammars = initGrammars;
exports.loadGrammarsForLanguages = loadGrammarsForLanguages;
exports.loadAllGrammars = loadAllGrammars;
exports.isGrammarsInitialized = isGrammarsInitialized;
exports.getParser = getParser;
exports.detectLanguage = detectLanguage;
exports.isLanguageSupported = isLanguageSupported;
exports.isGrammarLoaded = isGrammarLoaded;
exports.isFileLevelOnlyLanguage = isFileLevelOnlyLanguage;
exports.getSupportedLanguages = getSupportedLanguages;
exports.resetParser = resetParser;
exports.clearParserCache = clearParserCache;
exports.getUnavailableGrammarErrors = getUnavailableGrammarErrors;
exports.getLanguageDisplayName = getLanguageDisplayName;
const path = __importStar(require("path"));
const web_tree_sitter_1 = require("web-tree-sitter");
/**
 * WASM filename map — maps each language to its .wasm grammar file
 * in the tree-sitter-wasms package.
 */
const WASM_GRAMMAR_FILES = {
    typescript: 'tree-sitter-typescript.wasm',
    tsx: 'tree-sitter-tsx.wasm',
    javascript: 'tree-sitter-javascript.wasm',
    jsx: 'tree-sitter-javascript.wasm',
    python: 'tree-sitter-python.wasm',
    go: 'tree-sitter-go.wasm',
    rust: 'tree-sitter-rust.wasm',
    java: 'tree-sitter-java.wasm',
    c: 'tree-sitter-c.wasm',
    cpp: 'tree-sitter-cpp.wasm',
    csharp: 'tree-sitter-c_sharp.wasm',
    php: 'tree-sitter-php.wasm',
    ruby: 'tree-sitter-ruby.wasm',
    swift: 'tree-sitter-swift.wasm',
    kotlin: 'tree-sitter-kotlin.wasm',
    dart: 'tree-sitter-dart.wasm',
    pascal: 'tree-sitter-pascal.wasm',
    scala: 'tree-sitter-scala.wasm',
    lua: 'tree-sitter-lua.wasm',
    r: 'tree-sitter-r.wasm',
    luau: 'tree-sitter-luau.wasm',
    objc: 'tree-sitter-objc.wasm',
};
/**
 * File extension to Language mapping
 */
exports.EXTENSION_MAP = {
    '.ts': 'typescript',
    '.tsx': 'tsx',
    // ESM/CJS TypeScript module extensions — parsed as TS (no JSX). (#366)
    '.mts': 'typescript',
    '.cts': 'typescript',
    '.js': 'javascript',
    '.mjs': 'javascript',
    '.cjs': 'javascript',
    // SAP HANA XS Classic server-side JavaScript. (#556)
    '.xsjs': 'javascript',
    '.xsjslib': 'javascript',
    '.jsx': 'jsx',
    '.py': 'python',
    '.pyw': 'python',
    '.go': 'go',
    '.rs': 'rust',
    '.java': 'java',
    '.c': 'c',
    '.h': 'c', // Could also be C++, defaulting to C
    '.cpp': 'cpp',
    '.cc': 'cpp',
    '.cxx': 'cpp',
    '.hpp': 'cpp',
    '.hxx': 'cpp',
    '.cs': 'csharp',
    // ASP.NET Razor / Blazor markup — custom RazorExtractor (links @model/@inject/
    // component tags to their C# types; markup isn't a tree-sitter grammar).
    '.cshtml': 'razor',
    '.razor': 'razor',
    '.php': 'php',
    // Drupal-specific PHP file extensions
    '.module': 'php',
    '.install': 'php',
    '.theme': 'php',
    '.inc': 'php',
    // YAML (used for Drupal routing files; no symbol extraction, file-level tracking only)
    '.yml': 'yaml',
    '.yaml': 'yaml',
    // Twig templates (file-level tracking only, no symbol extraction)
    '.twig': 'twig',
    '.rb': 'ruby',
    '.rake': 'ruby',
    '.swift': 'swift',
    '.kt': 'kotlin',
    '.kts': 'kotlin',
    '.dart': 'dart',
    '.liquid': 'liquid',
    '.svelte': 'svelte',
    '.vue': 'vue',
    '.astro': 'astro',
    '.r': 'r',
    '.pas': 'pascal',
    '.dpr': 'pascal',
    '.dpk': 'pascal',
    '.lpr': 'pascal',
    '.dfm': 'pascal',
    '.fmx': 'pascal',
    '.scala': 'scala',
    '.sc': 'scala',
    '.lua': 'lua',
    '.luau': 'luau',
    '.m': 'objc',
    '.mm': 'objc',
    // XML: file-level tracking; the MyBatis extractor matches `<mapper namespace="...">`
    // shape and emits SQL-statement nodes (other XML returns empty).
    '.xml': 'xml',
    // Spring config: `application.properties` / `application-*.properties`. Same
    // shape as the `.yml` variants — the YAML/properties extractor emits one node
    // per leaf key, and the Spring resolver links `@Value("${k}")` references.
    '.properties': 'properties',
};
/**
 * Whether a file is one CodeGraph can parse, based purely on its extension.
 * This is the single source of truth for "should we index this file" — derived
 * from EXTENSION_MAP so parser support and indexing selection never drift.
 */
function isSourceFile(filePath) {
    if (isPlayRoutesFile(filePath))
        return true; // Play `conf/routes` is extensionless
    if (isShopifyLiquidJson(filePath))
        return true; // Shopify OS 2.0 JSON templates / section groups
    const dot = filePath.lastIndexOf('.');
    if (dot < 0)
        return false;
    return filePath.slice(dot).toLowerCase() in exports.EXTENSION_MAP;
}
/**
 * Shopify OS 2.0 JSON template (`templates/*.json`) or section group
 * (`sections/*.json`) — these reference sections by `"type"`, so the Liquid
 * extractor links them. (config/ + locales/ JSON have no section refs.)
 */
function isShopifyLiquidJson(filePath) {
    // Allow nested template dirs (`templates/customers/login.json`), not just
    // top-level (`templates/product.json`).
    return /(^|\/)(templates|sections)\/.+\.json$/i.test(filePath);
}
/**
 * Play Framework routes file: the extensionless `conf/routes` (and included
 * `conf/*.routes`). No grammar — route extraction is done by the Play framework
 * resolver, so it's processed through the no-grammar (`yaml`-style) path.
 */
function isPlayRoutesFile(filePath) {
    return (filePath === 'conf/routes' ||
        filePath.endsWith('/conf/routes') ||
        filePath.endsWith('.routes'));
}
/**
 * Caches for loaded grammars and parsers
 */
const parserCache = new Map();
const languageCache = new Map();
const unavailableGrammarErrors = new Map();
let parserInitialized = false;
/**
 * Initialize the tree-sitter WASM runtime. Must be called before loading grammars.
 * Does NOT load any grammar WASM files — use loadGrammarsForLanguages() for that.
 * Idempotent — safe to call multiple times.
 */
async function initGrammars() {
    if (parserInitialized)
        return;
    await web_tree_sitter_1.Parser.init();
    parserInitialized = true;
}
/**
 * Load grammar WASM files for specific languages only.
 * Skips languages that are already loaded or have no WASM grammar.
 * Must be called after initGrammars().
 */
async function loadGrammarsForLanguages(languages) {
    if (!parserInitialized) {
        await initGrammars();
    }
    // SFC languages (svelte/vue/astro) have no grammar of their own — their
    // extractors delegate <script>/frontmatter content to the TS/JS extractor,
    // so those grammars must be loaded even when no plain .ts/.js file is in
    // the index set (e.g. a pure-.astro content site).
    if (languages.some((l) => l === 'svelte' || l === 'vue' || l === 'astro')) {
        languages = [...languages, 'typescript', 'javascript'];
    }
    // Deduplicate and filter to languages that have WASM grammars and aren't already loaded
    const toLoad = [...new Set(languages)].filter((lang) => lang in WASM_GRAMMAR_FILES &&
        !languageCache.has(lang) &&
        !unavailableGrammarErrors.has(lang));
    // Load grammars sequentially to avoid web-tree-sitter WASM race condition on Node 20+
    // See: https://github.com/tree-sitter/tree-sitter/issues/2338
    for (const lang of toLoad) {
        const wasmFile = WASM_GRAMMAR_FILES[lang];
        try {
            // Some grammars ship their own WASMs (not in tree-sitter-wasms, or the
            // tree-sitter-wasms build is too old). Lua: tree-sitter-wasms ships an
            // ABI-13 build that corrupts the shared WASM heap under web-tree-sitter
            // 0.25 (drops nested calls/imports on every file after the first); we
            // vendor the upstream ABI-15 wasm instead. C#: the tree-sitter-wasms
            // build (ABI 13) has no primary-constructor support and parses
            // `class Foo(...)` as an ERROR that swallows the whole class (#237); we
            // vendor the upstream ABI-15 tree-sitter-c-sharp 0.23.5 wasm, which parses
            // primary constructors natively.
            const wasmPath = (lang === 'pascal' || lang === 'scala' || lang === 'lua' || lang === 'luau' || lang === 'csharp' || lang === 'r')
                ? path.join(__dirname, 'wasm', wasmFile)
                : require.resolve(`tree-sitter-wasms/out/${wasmFile}`);
            const language = await web_tree_sitter_1.Language.load(wasmPath);
            languageCache.set(lang, language);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            console.warn(`[CodeGraph] Failed to load ${lang} grammar — parsing will be unavailable: ${message}`);
            unavailableGrammarErrors.set(lang, message);
        }
    }
}
/**
 * Load ALL grammar WASM files. Convenience function for tests and
 * backward compatibility. Prefer loadGrammarsForLanguages() in production.
 */
async function loadAllGrammars() {
    const allLanguages = Object.keys(WASM_GRAMMAR_FILES);
    await loadGrammarsForLanguages(allLanguages);
}
/**
 * Check if grammars have been initialized
 */
function isGrammarsInitialized() {
    return parserInitialized;
}
/**
 * Get a parser for the specified language.
 * Returns synchronously from pre-loaded cache.
 */
function getParser(language) {
    if (parserCache.has(language)) {
        return parserCache.get(language);
    }
    const lang = languageCache.get(language);
    if (!lang) {
        return null;
    }
    const parser = new web_tree_sitter_1.Parser();
    parser.setLanguage(lang);
    parserCache.set(language, parser);
    return parser;
}
/**
 * Detect language from file extension
 */
function detectLanguage(filePath, source) {
    // Play `conf/routes` has no grammar — route through the no-symbol path; the
    // Play framework resolver extracts route nodes from it.
    if (isPlayRoutesFile(filePath))
        return 'yaml';
    const ext = filePath.substring(filePath.lastIndexOf('.')).toLowerCase();
    // Shopify OS 2.0 JSON templates / section groups → the Liquid extractor (it
    // links each section `"type"` to its `sections/<type>.liquid`).
    if (isShopifyLiquidJson(filePath))
        return 'liquid';
    const lang = exports.EXTENSION_MAP[ext] || 'unknown';
    // .h files could be C, C++, or Objective-C — check source content
    if (lang === 'c' && ext === '.h' && source) {
        if (looksLikeCpp(source))
            return 'cpp';
        if (looksLikeObjc(source))
            return 'objc';
    }
    return lang;
}
/**
 * Heuristic: does a .h file contain C++ constructs?
 * Checks the first ~8KB for patterns that are unique to C++ and never valid C.
 */
function looksLikeCpp(source) {
    const sample = source.substring(0, 8192);
    return /\bnamespace\b|\bclass\s+\w+\s*[:{]|\btemplate\s*<|\b(?:public|private|protected)\s*:|\bvirtual\b|\busing\s+(?:namespace\b|\w+\s*=)/.test(sample);
}
/**
 * Heuristic: does a .h file contain Objective-C constructs?
 */
function looksLikeObjc(source) {
    const sample = source.substring(0, 8192);
    return /@(?:interface|implementation|protocol|synthesize)\b/.test(sample);
}
/**
 * Check if a language is supported (has a grammar defined).
 * Returns true if the grammar exists, even if not yet loaded.
 */
function isLanguageSupported(language) {
    if (language === 'svelte')
        return true; // custom extractor (script block delegation)
    if (language === 'vue')
        return true; // custom extractor (script block delegation)
    if (language === 'astro')
        return true; // custom extractor (frontmatter/script block delegation)
    if (language === 'liquid')
        return true; // custom regex extractor
    if (language === 'razor')
        return true; // custom RazorExtractor (.cshtml/.razor markup)
    if (language === 'yaml')
        return true; // file-level tracking only; Drupal routing extraction via framework resolver
    if (language === 'twig')
        return true; // file-level tracking only
    if (language === 'xml')
        return true; // MyBatis mapper extractor
    if (language === 'properties')
        return true; // Spring config keys
    if (language === 'unknown')
        return false;
    return language in WASM_GRAMMAR_FILES;
}
/**
 * Check if a grammar has been loaded and is ready for parsing.
 */
function isGrammarLoaded(language) {
    if (language === 'svelte' || language === 'vue' || language === 'astro' || language === 'liquid' || language === 'razor')
        return true;
    if (language === 'yaml' || language === 'twig')
        return true; // no WASM grammar needed
    if (language === 'xml' || language === 'properties')
        return true; // no WASM grammar needed
    return languageCache.has(language);
}
/**
 * Languages tracked at the file-record level only: parsing emits zero symbol
 * nodes, but the file is still stored (and framework resolvers may add per-file
 * references later, e.g. Drupal routing yml, Spring `@Value` against
 * application.properties). This is the canonical set behind the no-symbol
 * branch in `tree-sitter.ts`; `xml` is intentionally excluded because its
 * MyBatis extractor emits a file node. Callers use this to count such files as
 * indexed rather than skipped, so it must stay in sync with that branch.
 */
function isFileLevelOnlyLanguage(language) {
    return language === 'yaml' || language === 'twig' || language === 'properties';
}
/**
 * Get all supported languages (those with grammar definitions).
 */
function getSupportedLanguages() {
    return [...Object.keys(WASM_GRAMMAR_FILES), 'svelte', 'vue', 'astro', 'liquid'];
}
/**
 * Reset the cached parser for a language to reclaim WASM heap memory.
 * The tree-sitter WASM runtime accumulates fragmented memory over thousands
 * of parses. Deleting and recreating the Parser instance forces the WASM
 * heap to reset, preventing "memory access out of bounds" crashes in
 * large repos.
 */
function resetParser(language) {
    const old = parserCache.get(language);
    if (old) {
        old.delete();
        parserCache.delete(language);
    }
}
/**
 * Clear parser/grammar caches (useful for testing)
 */
function clearParserCache() {
    for (const parser of parserCache.values()) {
        parser.delete();
    }
    parserCache.clear();
    // Note: languageCache is NOT cleared — WASM languages persist.
    // To fully re-init, set parserInitialized = false and call initGrammars() again.
    unavailableGrammarErrors.clear();
}
/**
 * Report grammars that failed to load.
 */
function getUnavailableGrammarErrors() {
    const out = {};
    for (const [language, message] of unavailableGrammarErrors.entries()) {
        out[language] = message;
    }
    return out;
}
/**
 * Get language display name
 */
function getLanguageDisplayName(language) {
    const names = {
        typescript: 'TypeScript',
        javascript: 'JavaScript',
        tsx: 'TypeScript (TSX)',
        jsx: 'JavaScript (JSX)',
        python: 'Python',
        go: 'Go',
        rust: 'Rust',
        r: 'R',
        java: 'Java',
        c: 'C',
        cpp: 'C++',
        csharp: 'C#',
        razor: 'Razor/Blazor',
        php: 'PHP',
        ruby: 'Ruby',
        swift: 'Swift',
        kotlin: 'Kotlin',
        dart: 'Dart',
        svelte: 'Svelte',
        vue: 'Vue',
        astro: 'Astro',
        liquid: 'Liquid',
        pascal: 'Pascal / Delphi',
        scala: 'Scala',
        lua: 'Lua',
        luau: 'Luau',
        objc: 'Objective-C',
        yaml: 'YAML',
        twig: 'Twig',
        xml: 'XML',
        properties: 'Java properties',
        unknown: 'Unknown',
    };
    return names[language] || language;
}
//# sourceMappingURL=grammars.js.map