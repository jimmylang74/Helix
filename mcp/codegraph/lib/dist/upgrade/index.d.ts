/**
 * `codegraph upgrade`
 *
 * Self-update for the CLI, whatever way it was installed:
 *
 *   - **bundle** — the self-contained runtime+app installed by `install.sh`
 *     (Linux/macOS) or `install.ps1` (Windows). Upgrading re-runs the SAME
 *     canonical installer script (single source of truth) so the download /
 *     version-resolution / PATH logic never drifts between first-install and
 *     upgrade.
 *   - **npm** — installed via `npm i -g @colbymchenry/codegraph`. Upgrading
 *     shells out to npm.
 *   - **npx** — ephemeral; nothing to upgrade (next `npx` fetches latest).
 *   - **source** — a git checkout running its own `dist/`; `git pull` + rebuild.
 *
 * Detection is structural (see `detectInstallMethod`): a bundle carries a
 * vendored `node` binary and a `bin/codegraph` launcher next to its `lib/`, so
 * we can recognize it from the running file's path without a marker file.
 *
 * Windows wrinkle: a running `node.exe` is locked and can't be deleted, so the
 * bundle's `current\` dir can't be overwritten in place by the process doing
 * the upgrade. We therefore spawn a DETACHED helper that waits for this
 * process to exit (releasing the lock), then runs `install.ps1`. This is the
 * conventional Windows self-update dance (rustup/nvm-windows do the same).
 */
export declare const REPO = "colbymchenry/codegraph";
export declare const NPM_PACKAGE = "@colbymchenry/codegraph";
export declare const INSTALL_SH_URL = "https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh";
export type InstallMethod = {
    kind: 'bundle';
    os: 'unix' | 'windows';
    bundleRoot: string;
    installDir: string | null;
} | {
    kind: 'npm';
    scope: 'global' | 'local';
} | {
    kind: 'npx';
} | {
    kind: 'source';
    root: string;
} | {
    kind: 'unknown';
    reason: string;
};
export interface DetectInput {
    /** `__filename` of the running CLI module — `<…>/dist/bin/codegraph.js`. */
    filename: string;
    platform: NodeJS.Platform;
    cwd: string;
    /** Injectable existence probe (defaults to fs.existsSync) — for tests. */
    exists?: (p: string) => boolean;
}
/**
 * Where the bundle installer keeps its install root, derived from the bundle
 * dir so an upgrade reuses a custom `CODEGRAPH_INSTALL_DIR`. Returns null when
 * the layout isn't the one the installer creates (then the installer falls
 * back to its own default).
 *
 *   unix:    <installDir>/versions/<vX.Y.Z>   (bundleRoot)  → <installDir>
 *   windows: <installDir>\current             (bundleRoot)  → <installDir>
 */
export declare function deriveInstallDir(bundleRoot: string, os: 'unix' | 'windows', exists: (p: string) => boolean): string | null;
export declare function detectInstallMethod(input: DetectInput): InstallMethod;
export interface Semver {
    major: number;
    minor: number;
    patch: number;
    pre: string | null;
}
export declare function parseSemver(version: string): Semver | null;
/** Returns >0 if a>b, <0 if a<b, 0 if equal. Throws on unparseable input. */
export declare function compareVersions(a: string, b: string): number;
export declare function isUpdateAvailable(current: string, latest: string): boolean;
/** `0.9.9` / `v0.9.9` → `v0.9.9` (release tags are v-prefixed). */
export declare function normalizeVersion(v: string): string;
/** Strip a leading `v`: `v0.9.9` → `0.9.9`. */
export declare function stripV(v: string): string;
/**
 * Parse the release tag out of the `Location` header GitHub returns for
 * `/releases/latest` → `…/releases/tag/v0.9.9`. Pure so it's unit-tested.
 */
export declare function parseLatestTagFromLocation(location: string | undefined): string | null;
/**
 * Resolve the latest release tag (e.g. `v0.9.9`).
 *
 * Primary: read the redirect `Location` from `github.com/<repo>/releases/latest`
 * — same trick install.sh uses, because the unauthenticated GitHub API is
 * rate-limited to 60 req/h/IP and 403s on shared/cloud hosts (issue #325). The
 * redirect has no such limit. Fall back to the API only if the redirect can't
 * be read.
 */
export declare function resolveLatestVersion(repo?: string, timeoutMs?: number): Promise<string>;
export interface UpgradeOptions {
    /** Pin a specific version (positional arg or CODEGRAPH_VERSION). */
    version?: string;
    /** Report current vs latest, don't change anything. */
    check?: boolean;
    /** Reinstall even if already on the resolved version. */
    force?: boolean;
}
/** Injectable side-effects so the orchestrator stays unit-testable. */
export interface UpgradeDeps {
    currentVersion: string;
    method: InstallMethod;
    resolveLatest: (pin?: string) => Promise<string>;
    /** Run a command inheriting stdio; returns its exit code (-1 = spawn failed). */
    run: (cmd: string, args: string[], env?: NodeJS.ProcessEnv) => number;
    hasCommand: (cmd: string) => boolean;
    log: (msg: string) => void;
    warn: (msg: string) => void;
    error: (msg: string) => void;
    platform: NodeJS.Platform;
}
/** The honest, additive re-index reminder shown after a successful upgrade. */
export declare function reindexAdvisory(): string;
/**
 * Returns the process exit code (0 = success / nothing to do, 1 = failure).
 */
export declare function runUpgrade(opts: UpgradeOptions, deps: UpgradeDeps): Promise<number>;
/** Build the in-place Windows upgrade script (exported for unit-testing). */
export declare function buildWindowsUpgradeScript(bundleRoot: string, version: string, arch: string): string;
/**
 * True if `cmd` resolves to an executable on PATH. A pure-Node PATH scan — NOT
 * a spawned `command -v`/`which`: `command` is a shell builtin (no standalone
 * binary on Debian, though macOS ships one), and `which` isn't guaranteed
 * present on minimal images, so spawning either is unreliable. Scanning PATH
 * ourselves behaves identically on every platform.
 */
export declare function hasCommand(cmd: string): boolean;
export declare function defaultRun(cmd: string, args: string[], env?: NodeJS.ProcessEnv): number;
//# sourceMappingURL=index.d.ts.map