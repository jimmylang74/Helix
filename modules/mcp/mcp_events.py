"""
Global event bus for MCP server status changes.

Unlike ``modules.agent.status_events`` (which is keyed per request_id), this
bus is global: every subscribed SSE client receives every MCP status event.
The reconnect monitor broadcasts on state transitions (connected <-> 
disconnected) only — never periodically — so subscribers rely on receiving a
full snapshot first (yielded by the Flask endpoint) and incremental updates
afterwards.
"""

import json
import queue
import threading
from typing import Any, Generator

_lock = threading.Lock()
_subscribers: list[queue.Queue[Any]] = []


def subscribe() -> queue.Queue[Any]:
    """Register a subscriber queue."""
    q: queue.Queue[Any] = queue.Queue()
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue[Any]) -> None:
    """Remove a subscriber queue and unblock a blocked stream() reader."""
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass
    try:
        q.put_nowait(None)
    except queue.Full:
        pass


def broadcast(payload: dict[str, Any]) -> None:
    """Push an event payload to every subscriber (non-blocking)."""
    data = json.dumps(payload, ensure_ascii=False)
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(data)
        except queue.Full:
            pass


def stream(keepalive: float = 30.0) -> Generator[str, None, None]:
    """SSE generator: live events with periodic keepalive comments."""
    q = subscribe()
    yield ": connected\n\n"
    try:
        while True:
            try:
                data = q.get(timeout=keepalive)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if data is None:
                break
            yield f"data: {data}\n\n"
    finally:
        unsubscribe(q)
