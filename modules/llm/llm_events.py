"""
Thread-safe in-memory event bus for streaming LLM events to the frontend.

Each request_id gets its own queue. The SSE endpoint reads from the queue
and streams events to the browser. LLMClient emits events here during
_call_engine() via the StdoutEventEmitter wrapper.
"""

import json
import queue
import threading
from typing import Any, Dict, Generator, Optional

# Per-request event queues: { request_id: [Queue, Queue, ...] }
_queues: Dict[str, list] = {}
_lock = threading.Lock()

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
    """Register a new consumer queue for a request and return it."""
    q: queue.Queue = queue.Queue()
    _get_queues(request_id).append(q)
    return q


def cleanup(request_id: str) -> None:
    """Remove all queues for a completed request and send sentinel."""
    with _lock:
        queues = _queues.pop(request_id, [])
    for q in queues:
        try:
            q.put_nowait(None)  # sentinel: stream is done
        except queue.Full:
            pass


# ── Emit (called by LLMClient._call_engine) ────────────────────────

def emit(request_id: str, event: Dict[str, Any]) -> None:
    """Push a single NDJSON event to all consumer queues for this request.

    Called from the LLMClient thread (inside redirect_stdout).
    Non-blocking: if a queue is full the event is dropped for that consumer.
    """
    payload = json.dumps(event, ensure_ascii=False)
    with _lock:
        queues = _queues.get(request_id, [])
    for q in queues:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # slow consumer — drop rather than block LLM


def emit_done(request_id: str) -> None:
    """Signal that all events for this request have been emitted."""
    with _lock:
        queues = _queues.get(request_id, [])
    for q in queues:
        try:
            q.put_nowait(None)  # sentinel
        except queue.Full:
            pass


# ── SSE stream generator (used by Flask endpoint) ──────────────────

def stream(request_id: str, timeout: float = 120.0) -> Generator[str, None, None]:
    """Yield SSE-formatted strings for *request_id*.

    Blocks on each queue.get() until an event arrives or *timeout* seconds
    elapse.  Yields ``None`` sentinel as ``": keepalive\n\n"`` to prevent
    proxy/browser timeouts.

    Usage in Flask::

        @app.route("/api/llm-stream")
        def llm_stream():
            rid = request.args.get("request_id", "")
            return Response(stream(rid), mimetype="text/event-stream")
    """
    q = _register_queue(request_id)
    # Send an initial comment so the client knows the connection is alive
    yield ": connected\n\n"

    try:
        while True:
            try:
                data = q.get(timeout=timeout)
            except queue.Empty:
                # No event within timeout — send keepalive comment
                yield ": keepalive\n\n"
                continue

            if data is None:
                break

            yield f"data: {data}\n\n"
    finally:
        # Remove this queue from the consumer list
        with _lock:
            queues = _queues.get(request_id, [])
            try:
                queues.remove(q)
            except ValueError:
                pass
