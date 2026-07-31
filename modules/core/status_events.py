"""
Thread-safe in-memory event bus for streaming agent status changes to the frontend.

Each request_id gets its own queue and a ring buffer.  The SSE endpoint reads
from the queue and streams state snapshots to the browser.  The orchestrator
calls emit() whenever the agent state meaningfully changes.

On reconnect the client sends a ``cursor`` query parameter; the stream()
generator replays buffered events from that cursor onward before switching
to real-time queue consumption.
"""

import json
import queue
import threading
from collections import deque
from typing import Any, Dict, Generator, List, Optional

# Per-request event queues: { request_id: [Queue, ...] }
_queues: Dict[str, list] = {}
_lock = threading.Lock()

# Per-request ring buffers: { request_id: deque[(index, payload)] }
_buffers: Dict[str, deque] = {}
_buf_counters: Dict[str, int] = {}  # monotonically increasing event index
MAX_BUFFER = 500


# ── Queue management ────────────────────────────────────────────────

def _get_queues(request_id: str) -> list:
    with _lock:
        return _queues.setdefault(request_id, [])


def _register_queue(request_id: str) -> queue.Queue:
    """Register a new consumer queue for a request."""
    q: queue.Queue = queue.Queue()
    with _lock:
        _queues.setdefault(request_id, []).append(q)
    return q


def cleanup(request_id: str) -> None:
    with _lock:
        queues = _queues.pop(request_id, [])
        _buffers.pop(request_id, None)
        _buf_counters.pop(request_id, None)
    for q in queues:
        try:
            q.put_nowait(None)
        except queue.Full:
            pass


# ── Emit (called by orchestrator on state transitions) ──────────────

def emit(request_id: str, state: Dict[str, Any],
         graph_nodes: Optional[List[Dict[str, Any]]] = None,
         node_result: Optional[Dict[str, Any]] = None) -> None:
    with _lock:
        idx = _buf_counters.get(request_id, 0) + 1
        _buf_counters[request_id] = idx

    snapshot: Dict[str, Any] = {
        "type": "status",
        "cursor": idx,
        "request_id": request_id,
        "final_result": state.get("final_result", ""),
        "generated_files": state.get("generated_files", []),
        "error": state.get("error"),
        "orchestrator_phase": state.get("orchestrator_phase", ""),
    }
    if graph_nodes is not None:
        snapshot["task_graph_nodes"] = graph_nodes
    if node_result is not None:
        snapshot["node_result"] = node_result
    payload = json.dumps(snapshot, ensure_ascii=False)

    with _lock:
        buf = _buffers.setdefault(request_id, deque(maxlen=MAX_BUFFER))
        buf.append((idx, payload))
        queues = list(_queues.get(request_id, []))

    for q in queues:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


# ── SSE stream generator (used by Flask endpoint) ──────────────────

def stream(request_id: str, cursor: int = 0,
           timeout: float = 120.0) -> Generator[str, None, None]:
    q = _register_queue(request_id)
    yield ": connected\n\n"

    with _lock:
        buf = _buffers.get(request_id)
        buf_list = list(buf) if buf else []
    for _, payload in buf_list:
        yield f"data: {payload}\n\n"

    try:
        while True:
            try:
                data = q.get(timeout=timeout)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue

            if data is None:
                break

            yield f"data: {data}\n\n"
    finally:
        with _lock:
            queues = _queues.get(request_id, [])
            try:
                queues.remove(q)
            except ValueError:
                pass
