"""
Background log file watcher with SSE push support.

A daemon thread polls the log file for new lines and pushes them
to all connected consumer queues.  Each SSE endpoint registers a
queue via ``subscribe()``; the background thread handles the rest.
"""

import os
import queue
import threading
import time
from typing import Dict, Generator, List

# Per-file state
_watchers: Dict[str, "_LogWatcher"] = {}
_lock = threading.Lock()


class _LogWatcher:
    """Watches a single log file and distributes new lines to consumers."""

    def __init__(self, log_path: str, interval: float = 1.0):
        self.log_path = log_path
        self.interval = interval
        self._queues: List[queue.Queue] = []
        self._q_lock = threading.Lock()
        self._offset = 0          # file byte offset for next read
        self._initialized = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── Consumer management ──────────────────────────────────────

    def subscribe(self) -> queue.Queue:
        """Register a new consumer and return its queue.

        On first subscribe the background watcher thread is started.
        """
        q: queue.Queue = queue.Queue()
        with self._q_lock:
            self._queues.append(q)
        if self._thread is None or not self._thread.is_alive():
            self._start_thread()
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._q_lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    # ── Background polling ───────────────────────────────────────

    def _start_thread(self):
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name=f"log-watcher-{os.path.basename(self.log_path)}"
        )
        self._thread.start()

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _scan(self):
        if not os.path.exists(self.log_path):
            return

        with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
            # On first run, send the tail of the file so the UI has context
            if not self._initialized:
                f.seek(0, 2)          # seek to end
                size = f.tell()
                # Send last ~50 lines as initial snapshot
                tail_bytes = min(size, 50 * 500)  # ~50 lines * ~500 chars avg
                f.seek(max(0, size - tail_bytes))
                initial_lines = f.readlines()
                if initial_lines:
                    self._broadcast(initial_lines)
                self._offset = f.tell()
                self._initialized = True
                return

            f.seek(self._offset)
            new_lines = f.readlines()
            if new_lines:
                self._offset = f.tell()
                self._broadcast(new_lines)

    def _broadcast(self, lines: List[str]):
        payload_lines = [line.rstrip("\n") for line in lines]
        import json
        payload = json.dumps({"type": "log", "lines": payload_lines}, ensure_ascii=False)
        with self._q_lock:
            queues = list(self._queues)
        for q in queues:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


# ── Public API ──────────────────────────────────────────────────────

def subscribe(log_file: str = "debugout.log", project_root: str = "") -> queue.Queue:
    """Subscribe to a log file and return a consumer queue.

    Args:
        log_file: Filename (relative to project root) or absolute path.
        project_root: Used to resolve relative log_file paths.
    """
    if os.path.isabs(log_file):
        log_path = log_file
    else:
        log_path = os.path.join(project_root, log_file) if project_root else log_file

    with _lock:
        if log_path not in _watchers:
            _watchers[log_path] = _LogWatcher(log_path)
        return _watchers[log_path].subscribe()


def unsubscribe(q: queue.Queue, log_file: str = "debugout.log", project_root: str = "") -> None:
    """Unsubscribe a queue from a log file."""
    if os.path.isabs(log_file):
        log_path = log_file
    else:
        log_path = os.path.join(project_root, log_file) if project_root else log_file

    with _lock:
        watcher = _watchers.get(log_path)
    if watcher:
        watcher.unsubscribe(q)


def stream(log_file: str = "debugout.log", project_root: str = "",
           timeout: float = 120.0) -> Generator[str, None, None]:
    """SSE stream generator for a log file.

    Usage in Flask::

        @app.route("/api/log-stream")
        def log_stream():
            return Response(
                stream("debugout.log", project_root),
                mimetype="text/event-stream",
            )
    """
    q = subscribe(log_file, project_root)
    yield ": connected\n\n"

    try:
        while True:
            try:
                data = q.get(timeout=timeout)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue

            yield f"data: {data}\n\n"
    finally:
        unsubscribe(q, log_file, project_root)
