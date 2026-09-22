/**
 * Directory Management
 *
 * Manages the .codegraph/ directory structure for CodeGraph data.
 */
/**
 * Resolve the per-project data directory name, honoring the `CODEGRAPH_DIR`
 * environment override (default `.codegraph`). The override is a single path
 * segment that lives in the project root.
 *
 * Why this exists: two environments that share one working tree must NOT share
 * one `.codegraph/` — most concretely Windows-native and WSL (issue #636). The
 * daemon lockfile (`.codegraph/daemon.pid`) records a platform-specific pid and
 * socket path (a Windows named pipe vs a WSL Unix socket), and SQLite file
 * locking across the WSL2 ↔ Windows filesystem boundary is unreliable, so two
 * daemons sharing one index risks corruption. Setting `CODEGRAPH_DIR=.codegraph-win`
 * on one side gives each environment its own index in the same tree.
 *
 * Read live (not captured at load) so it is both process-accurate and testable.
 * An override that isn't a plain directory name — empty, containing a path
 * separator, `.`, `..`/traversal, or absolute — is ignored (we keep the
 * default) rather than risk writing the index outside the project or into the
 * project root itself; we warn once to stderr so the misconfiguration is seen.
 */
export declare function codeGraphDirName(): string;
/**
 * CodeGraph directory name — a load-time snapshot of {@link codeGraphDirName}.
 * A running process's environment is fixed, so this equals the live value;
 * it's kept as a stable string export for backward compatibility. Internal code
 * resolves the name through {@link codeGraphDirName} / {@link getCodeGraphDir}
 * so the `CODEGRAPH_DIR` override always applies.
 */
export declare const CODEGRAPH_DIR: string;
/**
 * Is `name` (a single path segment) a CodeGraph data directory? Matches the
 * default `.codegraph`, the active `CODEGRAPH_DIR` override, and any
 * `.codegraph-*` sibling. File-watching and the indexer skip ALL of these, so
 * when two environments share one working tree (Windows + WSL, issue #636)
 * neither indexes or watches the other's index directory.
 */
export declare function isCodeGraphDataDir(name: string): boolean;
/**
 * Get the .codegraph directory path for a project
 */
export declare function getCodeGraphDir(projectRoot: string): string;
/**
 * Check if a project has been initialized with CodeGraph
 * Requires both .codegraph/ directory AND codegraph.db to exist
 */
export declare function isInitialized(projectRoot: string): boolean;
/**
 * Find the nearest parent directory containing .codegraph/
 *
 * Walks up from the given path to find a CodeGraph-initialized project,
 * similar to how git finds .git/ directories.
 *
 * @param startPath - Directory to start searching from
 * @returns The project root containing .codegraph/, or null if not found
 */
/**
 * Reason a directory is unsafe to use as an index ROOT, or null when it's fine.
 *
 * Indexing your home directory or a filesystem root drags in caches, `Library`,
 * every other project, etc. — a multi-GB index, constant file-watcher churn, and
 * (pre-1.0 on macOS) a file-descriptor blowup that exhausted `kern.maxfiles` and
 * took unrelated apps / the whole machine down (#845). The classic trigger:
 * running the installer or `codegraph init` from `$HOME`, which auto-indexes the
 * current directory. These are never intended project roots, so the installer
 * and `init`/`index` refuse them (overridable with `--force`).
 *
 * Pure-ish (reads only `os.homedir()` + realpath) so it's easy to unit-test.
 * The returned string is a human phrase that slots into "… looks like {reason}".
 */
export declare function unsafeIndexRootReason(projectRoot: string): string | null;
export declare function findNearestCodeGraphRoot(startPath: string): string | null;
/**
 * Create the .codegraph directory structure
 * Note: Only throws if codegraph.db already exists, not just if .codegraph/ exists.
 */
export declare function createDirectory(projectRoot: string): void;
/**
 * Remove the .codegraph directory
 */
export declare function removeDirectory(projectRoot: string): void;
/**
 * Get all files in the .codegraph directory
 */
export declare function listDirectoryContents(projectRoot: string): string[];
/**
 * Get the total size of the .codegraph directory in bytes
 */
export declare function getDirectorySize(projectRoot: string): number;
/**
 * Ensure a subdirectory exists within .codegraph
 */
export declare function ensureSubdirectory(projectRoot: string, subdirName: string): string;
/**
 * Check if the .codegraph directory has valid structure
 */
export declare function validateDirectory(projectRoot: string): {
    valid: boolean;
    errors: string[];
};
//# sourceMappingURL=directory.d.ts.map