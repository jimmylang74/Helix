/**
 * Extraction Orchestrator
 *
 * Coordinates file scanning, parsing, and database storage.
 */
import * as fs from 'fs';
import { ExtractionResult, ExtractionError } from '../types';
import { QueryBuilder } from '../db/queries';
import { Ignore } from 'ignore';
/**
 * Progress callback for indexing operations
 */
export interface IndexProgress {
    phase: 'scanning' | 'parsing' | 'storing' | 'resolving';
    current: number;
    total: number;
    currentFile?: string;
}
/**
 * Result of an indexing operation
 */
export interface IndexResult {
    success: boolean;
    filesIndexed: number;
    filesSkipped: number;
    filesErrored: number;
    nodesCreated: number;
    edgesCreated: number;
    errors: ExtractionError[];
    durationMs: number;
}
/**
 * Result of a sync operation
 */
export interface SyncResult {
    filesChecked: number;
    filesAdded: number;
    filesModified: number;
    filesRemoved: number;
    nodesUpdated: number;
    durationMs: number;
    changedFilePaths?: string[];
}
/**
 * Calculate SHA256 hash of file contents
 */
export declare function hashContent(content: string): string;
/**
 * An `ignore` matcher seeded with the built-in defaults, merged with the project's
 * root .gitignore so a negation there (e.g. `!vendor/`) overrides a default. Shared
 * by both enumeration paths so behavior is identical with or without git — and so
 * the defaults apply to tracked files too (committing a dependency dir doesn't make
 * it project code; the explicit `.gitignore` negation is the only opt-in).
 */
export declare function buildDefaultIgnore(rootDir: string): Ignore;
/**
 * Workspace-scope ignore matcher. Ordinary paths get the root's matcher
 * (built-in defaults + root `.gitignore`); paths inside an EMBEDDED repo get
 * that repo's own matcher (defaults + its root `.gitignore`) — the parent's
 * `.gitignore` hides a child repo from git, not from the index (#514). A
 * directory path (trailing slash) that is an ANCESTOR of an embedded root is
 * never ignored, so directory-pruning callers (the Linux per-directory
 * watcher) still descend to reach the embedded repos.
 *
 * Single source of truth for indexer and watcher scope — they must not diverge.
 */
export declare class ScopeIgnore {
    private rootMatcher;
    private embedded;
    private defaults;
    constructor(rootMatcher: Ignore, embedded: Array<{
        root: string;
        matcher: Ignore;
    }>);
    ignores(rel: string): boolean;
}
/**
 * Build the workspace-scope matcher. When the caller already knows the
 * embedded roots (the scanner discovers them during collection), pass them to
 * skip rediscovery; otherwise they're discovered here (the watcher path).
 */
export declare function buildScopeIgnore(rootDir: string, embeddedRoots?: Iterable<string>): ScopeIgnore;
/**
 * Standalone discovery of every embedded repo root under `rootDir` (relative,
 * trailing-slashed) — both the untracked kind (#193) and the gitignored kind
 * (#514), recursively (an embedded repo can embed further repos). Returns []
 * for non-git roots: the filesystem walk handles nested repos there already.
 */
export declare function discoverEmbeddedRepoRoots(rootDir: string): string[];
/**
 * Recursively scan a directory for source files.
 *
 * In git repos, uses `git ls-files` (inherently respects .gitignore at all
 * levels), then keeps files with a supported source extension. For non-git
 * projects, falls back to a filesystem walk that parses .gitignore itself.
 */
export declare function scanDirectory(rootDir: string, onProgress?: (current: number, file: string) => void): string[];
/**
 * Async variant of scanDirectory that yields to the event loop periodically,
 * allowing worker threads to receive and render progress messages.
 */
export declare function scanDirectoryAsync(rootDir: string, onProgress?: (current: number, file: string) => void): Promise<string[]>;
/**
 * Extraction orchestrator
 */
export declare class ExtractionOrchestrator {
    private rootDir;
    private queries;
    /**
     * Names of frameworks detected for this project, populated by indexAll().
     * Passed to extractFromSource so framework-specific extractors (route nodes,
     * middleware, etc.) run after the tree-sitter pass. Cleared if detection
     * hasn't run yet so single-file re-index paths can detect on the spot.
     */
    private detectedFrameworkNames;
    constructor(rootDir: string, queries: QueryBuilder);
    /**
     * Build a filesystem-backed ResolutionContext sufficient for framework
     * detection. Graph-query methods (getNodesByName etc.) return empty because
     * the DB hasn't been populated yet, but detect() only uses readFile,
     * fileExists, and getAllFiles, so that's fine.
     */
    private buildDetectionContext;
    /**
     * Detect frameworks on demand using the current scanned files (or a fresh
     * scan if none are provided). Cached on the orchestrator so repeat calls
     * inside a single run don't re-scan.
     */
    private ensureDetectedFrameworks;
    /**
     * Index all files in the project
     */
    indexAll(onProgress?: (progress: IndexProgress) => void, signal?: AbortSignal, verbose?: boolean): Promise<IndexResult>;
    /**
     * Index specific files
     */
    indexFiles(filePaths: string[]): Promise<IndexResult>;
    /**
     * Index a single file
     */
    indexFile(relativePath: string): Promise<ExtractionResult>;
    /**
     * Index a single file with pre-read content and stats.
     * Used by the parallel batch reader to avoid redundant file I/O.
     */
    indexFileWithContent(relativePath: string, content: string, stats: fs.Stats): Promise<ExtractionResult>;
    /**
     * Store extraction result in database
     */
    private storeExtractionResult;
    /**
     * Sync the index with the current file state.
     *
     * Change detection is filesystem-based, never git: a (size, mtime) stat
     * pre-filter skips unchanged files, then a content-hash compare confirms real
     * changes. This works in non-git projects and catches committed changes from
     * `git pull`/`checkout`/`merge`/`rebase` that `git status` cannot see.
     */
    sync(onProgress?: (progress: IndexProgress) => void): Promise<SyncResult>;
    /**
     * Get files that have changed since last index.
     * Uses git status as a fast path when available, falling back to full scan.
     */
    getChangedFiles(): {
        added: string[];
        modified: string[];
        removed: string[];
    };
}
export { extractFromSource } from './tree-sitter';
export { detectLanguage, isSourceFile, isLanguageSupported, isGrammarLoaded, getSupportedLanguages, initGrammars, loadGrammarsForLanguages, loadAllGrammars } from './grammars';
//# sourceMappingURL=index.d.ts.map