"""
Thread-safe in-memory event bus for streaming agent status changes to the frontend.

Each request_id gets its own queue. The SSE endpoint reads from the queue
and streams state snapshots to the browser.  The orchestrator calls emit()
whenever the agent state meaningfully changes (todo advance, subtask
completion, phase transition, final result, etc.).
"""

import json
import queue
import threading
from typing import Any, Dict, Generator, Optional

# Per-request event queues: { request_id: [Queue, ...] }
_queues: Dict[str, list] = {}
_lock = threading.Lock()


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
    """Remove all queues for a completed request and send sentinel."""
    with _lock:
        queues = _queues.pop(request_id, [])
    for q in queues:
        try:
            q.put_nowait(None)  # sentinel: stream is done
        except queue.Full:
            pass


# ── Emit (called by orchestrator on state transitions) ──────────────

def emit(request_id: str, state: Dict[str, Any]) -> None:
    """Push a snapshot of the relevant status fields to all consumers.

    Only the fields the frontend cares about are sent to keep payloads small.
    """
    snapshot = {
        "type": "status",
        "request_id": request_id,
        "todo_list": state.get("todo_list", []),
        "current_todo_idx": state.get("current_todo_idx", -1),
        "todo_subtask_lists": state.get("todo_subtask_lists", []),
        "subtask_status": state.get("subtask_status", "idle"),
        "final_result": state.get("final_result", ""),
        "generated_files": state.get("generated_files", []),
        "error": state.get("error"),
        "orchestrator_phase": state.get("orchestrator_phase", ""),
    }
    payload = json.dumps(snapshot, ensure_ascii=False)
    with _lock:
        queues = _queues.get(request_id, [])
    for q in queues:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


# ── SSE stream generator (used by Flask endpoint) ──────────────────

def stream(request_id: str, timeout: float = 120.0) -> Generator[str, None, None]:
    """Yield SSE-formatted strings for *request_id*.

    Blocks on each queue.get() until an event arrives or *timeout* seconds
    elapse.  Yields keepalive comments to prevent proxy/browser timeouts.
    """
    q = _register_queue(request_id)
    yield ": connected\n\n"

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
