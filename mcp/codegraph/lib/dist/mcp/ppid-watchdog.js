"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.supervisionLostReason = supervisionLostReason;
/**
 * Returns a human-readable reason string when the process has lost its
 * supervisor and should shut down, or null while it is still supervised.
 */
function supervisionLostReason(state) {
    const { originalPpid, currentPpid, hostPpid, isAlive } = state;
    const platform = state.platform ?? process.platform;
    // POSIX: the parent dying reparents us, so ppid diverges. (Never on Windows.)
    if (currentPpid !== originalPpid) {
        return `ppid ${originalPpid} -> ${currentPpid}`;
    }
    // Windows: ppid is stable across parent death, so detect it by liveness.
    // Skip pid 0/1 — "unknown" and init are never a real Windows parent, and a
    // bogus liveness probe there must not trigger a shutdown.
    if (platform === 'win32' && originalPpid > 1 && !isAlive(originalPpid)) {
        return `parent pid ${originalPpid} exited`;
    }
    // Either platform: the host pid threaded past a launcher shim is gone.
    if (hostPpid !== null && !isAlive(hostPpid)) {
        return `host pid ${hostPpid} exited`;
    }
    return null;
}
//# sourceMappingURL=ppid-watchdog.js.map