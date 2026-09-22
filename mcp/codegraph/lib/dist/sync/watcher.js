"use strict";
/**
 * File Watcher
 *
 * Watches the project directory for file changes and triggers debounced sync
 * operations to keep the code graph up-to-date.
 *
 * Uses Node's built-in `fs.watch` directly (no third-party watcher, no native
 * addon) with a per-platform strategy chosen to keep the open-descriptor /
 * kernel-watch cost BOUNDED rather than growing with the number of files:
 *
 *   - macOS / Windows: a SINGLE recursive `fs.watch(root, {recursive:true})`.
 *     libuv maps this to one FSEvents stream (macOS) / one
 *     ReadDirectoryChangesW handle (Windows), so it costs O(1) descriptors no
 *     matter how large the tree. This is the fix for the macOS file-table
 *     exhaustion (#644 / #496 / #555 / #628): the previous watcher held one
 *     open fd PER WATCHED FILE on macOS (tens of thousands of REG fds), which
 *     exhausted `kern.maxfiles` and crashed unrelated processes system-wide.
 *
 *   - Linux: recursive `fs.watch` is unsupported, so we watch each (non-ignored)
 *     DIRECTORY with one inotify watch — O(directories), NOT O(files). New
 *     directories are picked up dynamically and an overall watch cap bounds
 *     inotify usage on pathological monorepos (#579). A single inotify watch on
 *     a directory already reports create/modify/delete for its children, so
 *     per-file watches are never needed.
 *
 * Excluded trees (node_modules/, dist/, .git/, …) are filtered via the
 * indexer's `buildScopeIgnore` (built-in default-ignore dirs + the project's
 * .gitignore) — on Linux they're never descended into (so they cost no watch),
 * and on macOS/Windows the single recursive stream still covers them but their
 * events are dropped before any sync is scheduled. Either way the watcher's
 * scope matches the indexer's (#276 / #407).
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
exports.FileWatcher = exports.LockUnavailableError = void 0;
exports.__emitWatchEventForTests = __emitWatchEventForTests;
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const extraction_1 = require("../extraction");
const errors_1 = require("../errors");
const utils_1 = require("../utils");
const directory_1 = require("../directory");
const watch_policy_1 = require("./watch-policy");
/**
 * Native recursive `fs.watch` is only reliable on macOS and Windows; on Linux
 * (and AIX) it throws `ERR_FEATURE_UNAVAILABLE_ON_PLATFORM`. We branch on this
 * to pick the recursive vs per-directory strategy.
 */
function supportsRecursiveWatch() {
    return process.platform === 'darwin' || process.platform === 'win32';
}
/**
 * Upper bound on simultaneously-watched directories on the Linux per-directory
 * path. Each is one inotify watch; the kernel's `fs.inotify.max_user_watches`
 * is the hard limit (commonly 8k–128k). We stop adding watches past this and
 * log once — partial live-watch (with `codegraph sync` as the backstop) is far
 * better than exhausting the user's inotify budget and breaking watching
 * system-wide (#579). Tunable via CODEGRAPH_MAX_DIR_WATCHES.
 */
const DEFAULT_MAX_DIR_WATCHES = 50_000;
function maxDirWatches() {
    const raw = process.env.CODEGRAPH_MAX_DIR_WATCHES;
    if (raw && /^\d+$/.test(raw)) {
        const n = Number(raw);
        if (n > 0)
            return n;
    }
    return DEFAULT_MAX_DIR_WATCHES;
}
/**
 * Test seam (see {@link __emitWatchEventForTests}). Maps a watcher's project
 * root to its live instance so tests can synthesize a change event
 * deterministically — real fs.watch delivery latency races under parallel
 * vitest (the reason the previous chokidar mock existed). Only populated under
 * a test runner, so production carries no bookkeeping or retained references.
 */
const liveWatchersForTests = new Map();
const IS_TEST_RUNTIME = !!(process.env.VITEST || process.env.NODE_ENV === 'test');
/**
 * Thrown by a `syncFn` to signal that the underlying sync couldn't acquire
 * the cross-process write lock (#449). The watcher treats this as "no
 * progress" — preserves `pendingFiles`, skips `onSyncComplete`, and the
 * `finally` block reschedules. Quiet (debug-only) because a long-running
 * external indexer can hit this every debounce cycle.
 */
class LockUnavailableError extends Error {
    constructor(message = 'CodeGraph file lock unavailable; another process is writing') {
        super(message);
        this.name = 'LockUnavailableError';
    }
}
exports.LockUnavailableError = LockUnavailableError;
/**
 * FileWatcher monitors a project directory for changes and triggers
 * debounced sync operations via a provided callback.
 *
 * Design goals:
 * - Bounded resource usage: O(1) descriptors on macOS/Windows (one recursive
 *   watch), O(directories) inotify watches on Linux — never O(files), which
 *   was the system-crashing fd leak on macOS (#644/#496/#555/#628).
 * - Debounced to avoid thrashing on rapid saves
 * - Filters to supported source files by extension
 * - Ignores .codegraph/ and .git/ regardless of .gitignore
 * - Tracks per-file pending state so MCP tools can flag stale results
 *   without blocking on a sync (issue #403)
 */
class FileWatcher {
    /** macOS/Windows: the single recursive watcher. Null on Linux. */
    recursiveWatcher = null;
    /** Linux: one watcher per watched directory (keyed by absolute path). */
    dirWatchers = new Map();
    /** Set once the per-directory watch cap is hit, so we log only once. */
    dirCapWarned = false;
    /** Test-only inert mode: started, but with no OS watcher installed. */
    inert = false;
    debounceTimer = null;
    /**
     * Files seen by the watcher since the last successful sync — populated on
     * every change event, cleared at the start of a sync, and re-populated by
     * events that arrive mid-sync (or restored on sync failure). Keyed by the
     * same project-relative POSIX path the rest of the codebase uses, so a
     * caller can intersect tool-response file paths against this map cheaply.
     */
    pendingFiles = new Map();
    /**
     * Wall-clock ms at which the in-flight sync began. Combined with
     * {@link pendingFiles}'s `lastSeenMs`, this distinguishes "still in the
     * debounce window" (lastSeen > syncStarted, sync hasn't started yet for
     * this edit) from "currently being indexed" (lastSeen <= syncStarted).
     */
    syncStartedMs = 0;
    syncing = false;
    stopped = false;
    /**
     * True once the initial watch set is established. Unlike the previous
     * chokidar implementation there is no asynchronous initial "crawl" emitting
     * an `add` per existing file — `fs.watch` only reports changes from the
     * moment it's installed — so this flips to true synchronously at the end of
     * `start()`. The startup reconcile against on-disk state is handled
     * separately by the engine's catch-up sync, not by the watcher.
     */
    ready = false;
    /**
     * Callbacks that resolve when the watch set is established. Used by tests
     * (and any production caller that cares about a clean baseline) to
     * deterministically gate on watcher readiness.
     */
    readyWaiters = [];
    // The shared scope matcher (built-in defaults + project .gitignore, with
    // embedded child repos matched by their OWN rules — #514), built once at
    // start(). Same source of truth the indexer uses, so watcher scope can
    // never diverge from index scope. An embedded repo created after start()
    // joins the scope on the next watcher restart / re-index.
    ignoreMatcher = null;
    projectRoot;
    debounceMs;
    syncFn;
    onSyncComplete;
    onSyncError;
    inertForTests;
    constructor(projectRoot, syncFn, options = {}) {
        this.projectRoot = projectRoot;
        this.syncFn = syncFn;
        this.debounceMs = options.debounceMs ?? 2000;
        this.onSyncComplete = options.onSyncComplete;
        this.onSyncError = options.onSyncError;
        this.inertForTests = options.inertForTests ?? false;
    }
    /**
     * Start watching for file changes.
     * Returns true if watching started successfully, false otherwise.
     */
    start() {
        if (this.recursiveWatcher || this.dirWatchers.size > 0 || this.inert)
            return true; // Already watching
        this.stopped = false;
        // Some environments make filesystem watching unusable — most notably
        // WSL2 /mnt/ drives, where the underlying fs.watch calls block long
        // enough to break MCP startup handshakes (issue #199). Skip watching
        // there; callers fall back to manual `codegraph sync` or git sync hooks.
        const disabledReason = (0, watch_policy_1.watchDisabledReason)(this.projectRoot);
        if (disabledReason) {
            (0, errors_1.logDebug)('File watcher disabled', { reason: disabledReason, projectRoot: this.projectRoot });
            return false;
        }
        // Reuse the indexer's ignore set so the watcher and indexer agree on scope.
        this.ignoreMatcher = (0, extraction_1.buildScopeIgnore)(this.projectRoot);
        try {
            if (this.inertForTests) {
                // Test-only: install no OS watcher; the seam drives events instead.
                this.inert = true;
            }
            else if (supportsRecursiveWatch()) {
                this.startRecursive();
            }
            else {
                this.startPerDirectory();
            }
            // No async crawl to wait on: as soon as the watch set is installed we
            // have a clean baseline (pendingFiles is only populated by post-start
            // events). Clear defensively and flip ready.
            this.pendingFiles.clear();
            this.ready = true;
            for (const cb of this.readyWaiters)
                cb();
            this.readyWaiters.length = 0;
            if (IS_TEST_RUNTIME)
                liveWatchersForTests.set(this.projectRoot, this);
            (0, errors_1.logDebug)('File watcher started', {
                projectRoot: this.projectRoot,
                debounceMs: this.debounceMs,
                mode: this.inertForTests ? 'inert' : supportsRecursiveWatch() ? 'recursive' : 'per-directory',
                watchedDirs: this.dirWatchers.size || undefined,
            });
            return true;
        }
        catch (err) {
            // Watcher setup failed (e.g., permission denied, missing directory).
            (0, errors_1.logWarn)('Could not start file watcher', { error: String(err) });
            this.stop();
            return false;
        }
    }
    /**
     * macOS/Windows: one recursive watcher for the whole tree. O(1) descriptors.
     * `filename` arrives relative to the project root (with subdirectories), so
     * it maps straight to a project-relative path.
     */
    startRecursive() {
        this.recursiveWatcher = fs.watch(this.projectRoot, { recursive: true, persistent: true }, (_event, filename) => {
            if (this.stopped || filename == null)
                return;
            this.handleChange((0, utils_1.normalizePath)(String(filename)));
        });
        this.recursiveWatcher.on('error', (err) => {
            (0, errors_1.logWarn)('File watcher error', { error: String(err) });
        });
    }
    /**
     * Linux: walk the (non-ignored) tree and watch each directory. One inotify
     * watch per directory reports create/modify/delete for that directory's
     * direct children, so we never watch individual files.
     */
    startPerDirectory() {
        this.watchTree(this.projectRoot, /* markExisting */ false);
    }
    /**
     * Add an inotify watch for `dir` and recurse into its non-ignored
     * subdirectories. When `markExisting` is true (a directory that appeared
     * AFTER startup), the source files already inside it are recorded as pending
     * — this closes the `mkdir + write` race where files created before the new
     * directory's watch is installed would otherwise be missed until the next
     * full sync. The initial startup walk passes false (the engine's catch-up
     * sync owns the baseline).
     */
    watchTree(dir, markExisting) {
        if (this.dirWatchers.has(dir))
            return;
        if (this.dirWatchers.size >= maxDirWatches()) {
            if (!this.dirCapWarned) {
                this.dirCapWarned = true;
                (0, errors_1.logWarn)('File watcher hit directory-watch cap; remaining subtrees rely on manual/periodic sync', {
                    cap: maxDirWatches(),
                });
            }
            return;
        }
        let w;
        try {
            w = fs.watch(dir, { persistent: true }, (_event, filename) => this.handleDirEvent(dir, filename));
        }
        catch {
            // ENOENT / EACCES / too-many-open-files — skip this directory quietly.
            return;
        }
        w.on('error', () => this.unwatchDir(dir));
        this.dirWatchers.set(dir, w);
        let entries;
        try {
            entries = fs.readdirSync(dir, { withFileTypes: true });
        }
        catch {
            return;
        }
        for (const entry of entries) {
            const child = path.join(dir, entry.name);
            if (entry.isDirectory()) {
                if (this.shouldIgnoreDir(child))
                    continue;
                this.watchTree(child, markExisting);
            }
            else if (markExisting && entry.isFile()) {
                this.handleChange((0, utils_1.normalizePath)(path.relative(this.projectRoot, child)));
            }
        }
    }
    /**
     * Linux per-directory event handler. `filename` is relative to `dir`. A new
     * sub-directory is picked up by extending the watch tree; everything else is
     * routed through the shared change handler.
     */
    handleDirEvent(dir, filename) {
        if (this.stopped || filename == null)
            return;
        const full = path.join(dir, String(filename));
        // A newly-created directory needs its own watch (recursive isn't available
        // on Linux). statSync is cheap and these events are rare relative to file
        // edits. If the path vanished (rapid create/delete) the stat throws and we
        // fall through to the change handler, which no-ops on a non-source path.
        try {
            if (fs.statSync(full).isDirectory()) {
                if (!this.shouldIgnoreDir(full))
                    this.watchTree(full, /* markExisting */ true);
                return;
            }
        }
        catch {
            // deleted/inaccessible — treat as a normal change below
        }
        this.handleChange((0, utils_1.normalizePath)(path.relative(this.projectRoot, full)));
    }
    /**
     * Shared change handler for both watch strategies. `rel` is a
     * project-relative POSIX path. Applies the ignore + source-file filters and,
     * for a real source change, records it as pending (#403) and schedules a
     * debounced sync.
     *
     * The recursive (macOS/Windows) watcher reports events for ignored trees too
     * (one stream covers the whole repo), so the ignore check here is load-bearing
     * — it drops node_modules/dist/.git churn before any sync is scheduled.
     */
    handleChange(rel) {
        if (!rel || rel === '.' || rel.startsWith('..'))
            return;
        if (this.isAlwaysIgnored(rel))
            return;
        if (this.ignoreMatcher && this.ignoreMatcher.ignores(rel))
            return;
        if (!(0, extraction_1.isSourceFile)(rel))
            return;
        (0, errors_1.logDebug)('File change detected', { file: rel });
        if (this.ready) {
            const now = Date.now();
            const existing = this.pendingFiles.get(rel);
            this.pendingFiles.set(rel, {
                firstSeenMs: existing?.firstSeenMs ?? now,
                lastSeenMs: now,
            });
        }
        this.scheduleSync();
    }
    /** Close and forget the watch for a directory that errored/was removed. */
    unwatchDir(dir) {
        const w = this.dirWatchers.get(dir);
        if (w) {
            try {
                w.close();
            }
            catch {
                /* already closed */
            }
            this.dirWatchers.delete(dir);
        }
    }
    /** Our own dirs are always ignored, regardless of .gitignore. */
    isAlwaysIgnored(rel) {
        // First path segment. Ignore any CodeGraph data dir — the active one AND a
        // sibling like `.codegraph-win` a second environment (Windows/WSL) created
        // in the same tree, so neither side watches the other's index (#636).
        const top = rel.split('/')[0] ?? rel;
        return ((0, directory_1.isCodeGraphDataDir)(top) ||
            rel === '.git' || rel.startsWith('.git/'));
    }
    /**
     * True for any directory that should NOT be watched (used while building the
     * Linux per-directory watch tree). Tests the directory form of the path so a
     * dir-only ignore rule like `build/` matches.
     */
    shouldIgnoreDir(dirPath) {
        const rel = (0, utils_1.normalizePath)(path.relative(this.projectRoot, dirPath));
        if (!rel || rel === '.' || rel.startsWith('..'))
            return false; // root / outside
        if (this.isAlwaysIgnored(rel))
            return true;
        if (!this.ignoreMatcher)
            return false;
        return this.ignoreMatcher.ignores(rel + '/');
    }
    /**
     * Stop watching for file changes.
     */
    stop() {
        this.stopped = true;
        if (this.debounceTimer) {
            clearTimeout(this.debounceTimer);
            this.debounceTimer = null;
        }
        if (this.recursiveWatcher) {
            try {
                this.recursiveWatcher.close();
            }
            catch {
                /* already closed */
            }
            this.recursiveWatcher = null;
        }
        for (const w of this.dirWatchers.values()) {
            try {
                w.close();
            }
            catch {
                /* already closed */
            }
        }
        this.dirWatchers.clear();
        this.dirCapWarned = false;
        this.inert = false;
        this.pendingFiles.clear();
        this.ready = false;
        this.ignoreMatcher = null;
        if (IS_TEST_RUNTIME)
            liveWatchersForTests.delete(this.projectRoot);
        (0, errors_1.logDebug)('File watcher stopped');
    }
    /**
     * @internal Test-only: feed a synthetic project-relative change through the
     * same filter → pendingFiles → debounced-sync path a real fs.watch event
     * takes. Lets the watcher / staleness-banner suites stay deterministic
     * instead of racing on OS watch-delivery latency. See
     * {@link __emitWatchEventForTests}.
     */
    ingestEventForTests(relPath) {
        this.handleChange((0, utils_1.normalizePath)(relPath));
    }
    /**
     * Whether the watcher is currently active.
     */
    isActive() {
        return (this.recursiveWatcher !== null || this.dirWatchers.size > 0 || this.inert) && !this.stopped;
    }
    /**
     * Resolves once the watch set has been installed (or immediately if it
     * already has). Useful for tests that need a deterministic boundary before
     * asserting on `pendingFiles`.
     *
     * Production callers don't need this: `pendingFiles` is read continuously,
     * the staleness banner is always correct (empty or populated), and there is
     * no asynchronous initial-scan window with `fs.watch`.
     */
    waitUntilReady(timeoutMs = 10000) {
        if (this.ready)
            return Promise.resolve();
        return new Promise((resolve, reject) => {
            const t = setTimeout(() => {
                const idx = this.readyWaiters.indexOf(handler);
                if (idx >= 0)
                    this.readyWaiters.splice(idx, 1);
                reject(new Error(`FileWatcher.waitUntilReady timed out after ${timeoutMs}ms`));
            }, timeoutMs);
            const handler = () => { clearTimeout(t); resolve(); };
            this.readyWaiters.push(handler);
        });
    }
    /**
     * Schedule a debounced sync.
     */
    scheduleSync() {
        if (this.debounceTimer) {
            clearTimeout(this.debounceTimer);
        }
        this.debounceTimer = setTimeout(() => {
            this.debounceTimer = null;
            this.flush();
        }, this.debounceMs);
    }
    /**
     * Flush pending changes by running sync.
     *
     * pendingFiles is NOT cleared at the start of sync — entries are removed
     * only after sync commits successfully, and only for entries whose
     * lastSeenMs <= syncStartedMs. That way, a query that arrives mid-sync
     * still sees the affected files marked stale (the DB hasn't been updated
     * yet), and an event that lands mid-sync persists into the follow-up.
     *
     * On sync failure pendingFiles is left untouched — every edit is still
     * unindexed, and the rescheduled sync will absorb the same set next time.
     */
    async flush() {
        // If already syncing, the post-sync check will re-trigger
        if (this.syncing || this.stopped)
            return;
        this.syncStartedMs = Date.now();
        this.syncing = true;
        try {
            const result = await this.syncFn();
            // Remove entries whose most recent event predates this sync — those
            // edits are now in the DB. Entries with lastSeenMs > syncStartedMs
            // arrived mid-sync; whether the in-flight sync captured them depends
            // on when sync read that file, so we keep them as pending and let
            // the follow-up sync handle them. We prefer false positives ("shown
            // stale, actually fresh" → at worst one extra Read) over false
            // negatives ("shown fresh, actually stale" → misleads the agent).
            for (const [filePath, info] of this.pendingFiles) {
                if (info.lastSeenMs <= this.syncStartedMs) {
                    this.pendingFiles.delete(filePath);
                }
            }
            this.onSyncComplete?.(result);
        }
        catch (err) {
            if (err instanceof LockUnavailableError) {
                // Lock-failure no-op (another writer holds the lock). pendingFiles
                // stays intact and the `finally` block reschedules. Debug-only —
                // a long external index would otherwise spam stderr every cycle.
                (0, errors_1.logDebug)('Watch sync skipped: file lock unavailable', {
                    pendingFiles: this.pendingFiles.size,
                });
            }
            else {
                const error = err instanceof Error ? err : new Error(String(err));
                (0, errors_1.logWarn)('Watch sync failed', { error: error.message });
                this.onSyncError?.(error);
            }
            // Failure: leave pendingFiles untouched. Every edit it tracks is
            // still unindexed; the rescheduled sync sees the same set.
        }
        finally {
            this.syncing = false;
            // If pending files remain (mid-sync events, or this sync failed),
            // schedule another pass.
            if (this.pendingFiles.size > 0 && !this.stopped) {
                this.scheduleSync();
            }
        }
    }
    /**
     * Snapshot of files seen by the watcher since the last successful sync.
     *
     * Used by MCP tool responses to mark stale results without blocking on a
     * sync: a tool that returns a hit in `src/foo.ts` while `src/foo.ts` is in
     * this list tells the agent "Read this file directly, the index lags."
     *
     * `indexing` is true when a sync is currently in flight whose start time is
     * AFTER this file's most recent event — i.e. that sync will absorb the
     * edit. False means the file is still inside the debounce window and no
     * sync has started yet (a follow-up call a few hundred ms later may show
     * `indexing: true` or the file may have left the list entirely).
     *
     * Cheap: O(pendingFiles.size), no I/O, no locks.
     */
    getPendingFiles() {
        const result = [];
        for (const [filePath, info] of this.pendingFiles) {
            result.push({
                path: filePath,
                firstSeenMs: info.firstSeenMs,
                lastSeenMs: info.lastSeenMs,
                indexing: this.syncing && this.syncStartedMs >= info.lastSeenMs,
            });
        }
        return result;
    }
}
exports.FileWatcher = FileWatcher;
/**
 * Test-only: synthesize a source-file change for the live watcher running at
 * `projectRoot`, exercising the real filter → pendingFiles → debounced-sync
 * logic without depending on fs.watch delivery timing (which races under
 * parallel vitest). `relPath` is project-relative POSIX (e.g. "src/foo.ts").
 * Returns false if no live watcher is registered for that root (e.g. outside a
 * test runtime, where the registry is intentionally not populated).
 */
function __emitWatchEventForTests(projectRoot, relPath) {
    const w = liveWatchersForTests.get(projectRoot);
    if (!w)
        return false;
    w.ingestEventForTests(relPath);
    return true;
}
//# sourceMappingURL=watcher.js.map