"use strict";
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
exports.getDaemonSocketPath = getDaemonSocketPath;
exports.getDaemonPidPath = getDaemonPidPath;
exports.encodeLockInfo = encodeLockInfo;
exports.decodeLockInfo = decodeLockInfo;
const crypto = __importStar(require("crypto"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
const directory_1 = require("../directory");
/** Soft upper bound for in-project socket paths. */
const POSIX_SOCKET_PATH_LIMIT = 100;
/** Short stable identifier for a project root — used in tmpdir/pipe names. */
function projectHash(projectRoot) {
    return crypto.createHash('sha256').update(path.resolve(projectRoot)).digest('hex').slice(0, 16);
}
/**
 * Compute the socket / named-pipe path the daemon should listen on (and the
 * proxy should connect to) for `projectRoot`. Deterministic given a project
 * root, so independent processes converge without coordination.
 */
function getDaemonSocketPath(projectRoot) {
    if (process.platform === 'win32') {
        return `\\\\.\\pipe\\codegraph-${projectHash(projectRoot)}`;
    }
    const inProject = path.join((0, directory_1.getCodeGraphDir)(projectRoot), 'daemon.sock');
    if (inProject.length <= POSIX_SOCKET_PATH_LIMIT)
        return inProject;
    // Long project paths (deep monorepos, Bazel out dirs) need tmpdir fallback
    // or `bind` returns EADDRINUSE / ENAMETOOLONG. Hash keeps it project-scoped.
    return path.join(os.tmpdir(), `codegraph-${projectHash(projectRoot)}.sock`);
}
/** Absolute path to the daemon pid lockfile for `projectRoot`. */
function getDaemonPidPath(projectRoot) {
    return path.join((0, directory_1.getCodeGraphDir)(projectRoot), 'daemon.pid');
}
/**
 * Serialize a {@link DaemonLockInfo} for writing to the pidfile. JSON for
 * human readability — operators occasionally `cat` this when debugging.
 */
function encodeLockInfo(info) {
    return JSON.stringify(info, null, 2) + '\n';
}
/**
 * Parse a pidfile body. Tolerant of old-format pidfiles (plain decimal pid) so
 * a 0.10.x daemon doesn't trip over a 0.9.x lockfile if that ever happens —
 * we treat such a lockfile as "process is unknown version, refuse to share."
 */
function decodeLockInfo(raw) {
    const trimmed = raw.trim();
    if (!trimmed)
        return null;
    try {
        const parsed = JSON.parse(trimmed);
        if (parsed &&
            typeof parsed.pid === 'number' &&
            typeof parsed.version === 'string' &&
            typeof parsed.socketPath === 'string' &&
            typeof parsed.startedAt === 'number') {
            return parsed;
        }
        return null;
    }
    catch {
        // Fall through to legacy plain-pid handling.
    }
    const pid = Number(trimmed);
    if (Number.isFinite(pid) && pid > 0) {
        return { pid, version: 'unknown', socketPath: '', startedAt: 0 };
    }
    return null;
}
//# sourceMappingURL=daemon-paths.js.map