/**
 * Daemon socket + lockfile path helpers — issue #411.
 *
 * One shared `codegraph serve --mcp` daemon per project root means we need a
 * stable, project-keyed rendezvous between cooperating processes. The IPC
 * surface area is just two file paths:
 *
 *   - `daemon.sock` — Unix domain socket / named pipe the daemon listens on.
 *   - `daemon.pid` — atomic-create lockfile holding the daemon's pid + version.
 *
 * Both live under `.codegraph/` so the project-scoped uninstall (`codegraph
 * uninit`) sweeps them up for free.
 *
 * Special-case: Unix domain socket paths have a hard length limit (~104 on
 * macOS, ~108 on Linux); when the in-project path exceeds it we fall back to
 * an absolute-path hash under `os.tmpdir()`. The pidfile always stays in the
 * project (it doesn't have a length limit) — and acts as the authoritative
 * pointer to the socket path the daemon chose.
 */
/**
 * Compute the socket / named-pipe path the daemon should listen on (and the
 * proxy should connect to) for `projectRoot`. Deterministic given a project
 * root, so independent processes converge without coordination.
 */
export declare function getDaemonSocketPath(projectRoot: string): string;
/** Absolute path to the daemon pid lockfile for `projectRoot`. */
export declare function getDaemonPidPath(projectRoot: string): string;
/** Structured contents of the pid lockfile. */
export interface DaemonLockInfo {
    pid: number;
    version: string;
    socketPath: string;
    startedAt: number;
}
/**
 * Serialize a {@link DaemonLockInfo} for writing to the pidfile. JSON for
 * human readability — operators occasionally `cat` this when debugging.
 */
export declare function encodeLockInfo(info: DaemonLockInfo): string;
/**
 * Parse a pidfile body. Tolerant of old-format pidfiles (plain decimal pid) so
 * a 0.10.x daemon doesn't trip over a 0.9.x lockfile if that ever happens —
 * we treat such a lockfile as "process is unknown version, refuse to share."
 */
export declare function decodeLockInfo(raw: string): DaemonLockInfo | null;
//# sourceMappingURL=daemon-paths.d.ts.map