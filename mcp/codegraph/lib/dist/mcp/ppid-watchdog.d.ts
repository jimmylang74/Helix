/**
 * Shared decision logic for the PPID watchdog (#277, #692).
 *
 * The watchdog's job: notice that the process we depend on — our parent, or the
 * MCP host reached past an intermediate launcher — has died, so an orphaned
 * proxy / direct server shuts itself down instead of leaking forever.
 *
 * Parent death surfaces differently per OS, and getting this wrong is what
 * caused the unbounded daemon/proxy leak on Windows (#692, #576):
 *
 *   - **POSIX** reparents an orphan to init (pid 1), so `process.ppid` *changes*
 *     the instant the parent dies. That divergence is the classic #277 signal.
 *   - **Windows** never reparents: `process.ppid` keeps reporting the original
 *     (now-dead) parent forever, so the change-check can never fire. There we
 *     must poll the original parent's *liveness* instead.
 *
 * The liveness fallback is deliberately gated to Windows. On POSIX a
 * double-forked grandparent can legitimately outlive the reparent, so a dead
 * `originalPpid` is not proof of orphaning there — the change-check is the
 * correct and sufficient POSIX signal, and using liveness too would risk a
 * false-positive shutdown.
 */
export interface SupervisionState {
    /** `process.ppid` captured at startup. */
    originalPpid: number;
    /** `process.ppid` right now. */
    currentPpid: number;
    /**
     * The MCP host pid threaded past an intermediate launcher
     * (`CODEGRAPH_HOST_PPID`), or null when unknown — e.g. the standalone bundle,
     * which pre-bakes `--liftoff-only` and so never runs the relaunch that sets it.
     */
    hostPpid: number | null;
    /** Liveness probe — `process.kill(pid, 0)` in production, stubbed in tests. */
    isAlive: (pid: number) => boolean;
    /** Defaults to `process.platform`. */
    platform?: NodeJS.Platform;
}
/**
 * Returns a human-readable reason string when the process has lost its
 * supervisor and should shut down, or null while it is still supervised.
 */
export declare function supervisionLostReason(state: SupervisionState): string | null;
//# sourceMappingURL=ppid-watchdog.d.ts.map