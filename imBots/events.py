"""
SSE event broadcasting for iBot messages.

Mirrors the log_watcher pattern: a per-channel queue registry so multiple
SSE clients can subscribe and receive real-time message pushes.
"""

import json
import queue
import threading
from typing import Any, Dict, Generator, List, Optional


# Per-channel state
_subscribers: Dict[str, List[queue.Queue[str]]] = {}
_lock = threading.Lock()

# Ring buffer for reconnect replay
_BUFFER_SIZE = 200
_buffers: Dict[str, List[tuple[int, str]]] = {}  # channel -> [(idx, payload)]
_buf_counters: Dict[str, int] = {}


def _ensure_channel(channel: str) -> None:
    if channel not in _subscribers:
        _subscribers[channel] = []
    if channel not in _buffers:
        _buffers[channel] = []
    if channel not in _buf_counters:
        _buf_counters[channel] = 0


def subscribe(channel: str) -> queue.Queue[str]:
    """Register a new SSE consumer and return its queue."""
    q: queue.Queue[str] = queue.Queue(maxsize=200)
    with _lock:
        _ensure_channel(channel)
        _subscribers[channel].append(q)
    return q


def unsubscribe(channel: str, q: queue.Queue[str]) -> None:
    with _lock:
        subs = _subscribers.get(channel, [])
        try:
            subs.remove(q)
        except ValueError:
            pass


def broadcast(channel: str, event_data: Dict[str, Any]) -> None:
    """Push an event to all SSE subscribers of a channel."""
    with _lock:
        _ensure_channel(channel)
        _buf_counters[channel] += 1
        idx = _buf_counters[channel]
        payload = json.dumps(event_data, ensure_ascii=False)
        _buffers[channel].append((idx, payload))
        # Trim ring buffer
        if len(_buffers[channel]) > _BUFFER_SIZE:
            _buffers[channel] = _buffers[channel][-_BUFFER_SIZE:]
        subs = list(_subscribers[channel])

    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


def stream(
    channel: str,
    cursor: int = 0,
    timeout: float = 60.0,
) -> Generator[str, None, None]:
    """SSE generator for Flask Response.

    Yields SSE-formatted strings: ``data: {json}\\n\\n``.
    """
    q = subscribe(channel)
    try:
        yield ": connected\n\n"

        # Replay buffered events since cursor
        with _lock:
            buf = _buffers.get(channel, [])
        for idx, payload in buf:
            if idx > cursor:
                yield f"data: {payload}\n\n"

        while True:
            try:
                data = q.get(timeout=timeout)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield f"data: {data}\n\n"
    finally:
        unsubscribe(channel, q)


def get_buffer_since(channel: str, cursor: int) -> List[str]:
    """Return buffered payloads with index > cursor (for reconnect replay)."""
    with _lock:
        buf = _buffers.get(channel, [])
    return [payload for idx, payload in buf if idx > cursor]
