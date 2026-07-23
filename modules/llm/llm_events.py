"""
Thread-safe in-memory event bus for streaming LLM events to the frontend.

Each request_id gets its own queue. The SSE endpoint reads from the queue
and streams events to the browser. LLMClient emits events here during
_call_engine() via the StdoutEventEmitter wrapper.
"""

import json
import queue
import threading
from collections import deque
from typing import Any, Dict, Generator, Optional

# Per-request event queues: { request_id: [Queue, Queue, ...] }
_queues: Dict[str, list] = {}
_lock = threading.Lock()

_buf_buffers: Dict[str, deque] = {}
_buf_counters: Dict[str, int] = {}
MAX_BUFFER = 500

# Thread-local storage for current request context
_ctx = threading.local()


# ── Request context (set by orchestrator, read by LLMClient) ────────

def set_request_context(request_id: str) -> None:
    """Set the active request_id for this thread."""
    _ctx.request_id = request_id


def get_request_context() -> Optional[str]:
    """Get the active request_id for this thread, or None."""
    return getattr(_ctx, "request_id", None)


def clear_request_context() -> None:
    """Clear the active request context for this thread."""
    _ctx.request_id = None


# ── Queue management ────────────────────────────────────────────────

def _get_queues(request_id: str) -> list:
    """Get all queues for a request_id (creates if needed)."""
    with _lock:
        return _queues.setdefault(request_id, [])


def _register_queue(request_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _lock:
        _queues.setdefault(request_id, []).append(q)
    return q


def cleanup(request_id: str) -> None:
    with _lock:
        queues = _queues.pop(request_id, [])
        _buf_buffers.pop(request_id, None)
        _buf_counters.pop(request_id, None)
    for q in queues:
        try:
            q.put_nowait(None)
        except queue.Full:
            pass


# ── Emit (called by LLMClient._call_engine) ────────────────────────

def emit(request_id: str, event: Dict[str, Any]) -> None:
    payload = json.dumps(event, ensure_ascii=False)

    with _lock:
        idx = _buf_counters.get(request_id, 0) + 1
        _buf_counters[request_id] = idx
        buf = _buf_buffers.setdefault(request_id, deque(maxlen=MAX_BUFFER))
        buf.append((idx, payload))
        queues = list(_queues.get(request_id, []))
    for q in queues:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


def emit_done(request_id: str) -> None:
    with _lock:
        queues = _queues.get(request_id, [])
    for q in queues:
        try:
            q.put_nowait(None)
        except queue.Full:
            pass


def _get_buffer_snapshot(request_id: str):
    with _lock:
        buf = _buf_buffers.get(request_id)
        return list(buf) if buf else []


# ── SSE stream generator (used by Flask endpoint) ──────────────────

def stream(request_id: str, cursor: int = 0, timeout: float = 120.0) -> Generator[str, None, None]:
    q = _register_queue(request_id)
    yield ": connected\n\n"

    for _, payload in _get_buffer_snapshot(request_id):
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
