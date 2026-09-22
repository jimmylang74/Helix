/** Default: 60s — ~300× shorter than the 5h #850 wedge, far longer than any real main-thread block. */
export declare const DEFAULT_WATCHDOG_TIMEOUT_MS = 60000;
/** Parse the timeout env, falling back to the default for missing/invalid values. */
export declare function parseWatchdogTimeoutMs(raw: string | undefined, fallback?: number): number;
/** Derive a heartbeat cadence that emits several beats inside the timeout window. */
export declare function deriveCheckIntervalMs(timeoutMs: number): number;
export interface WatchdogHandle {
    /** Stop heartbeating and shut the watchdog child down. Idempotent. */
    stop(): void;
}
/**
 * Install the main-thread liveness watchdog for a long-lived process. Returns a
 * handle to stop it, or `null` when disabled or when the child can't be spawned
 * (degraded, never throws — a missing watchdog must never keep a process from
 * starting).
 */
export declare function installMainThreadWatchdog(): WatchdogHandle | null;
//# sourceMappingURL=liveness-watchdog.d.ts.map