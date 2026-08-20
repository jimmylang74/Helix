"""Unit tests for imBots.events SSE broadcasting."""

import json
import queue
import threading

import pytest

import imBots.events as events_mod


@pytest.fixture(autouse=True)
def _clean_events():
    """Reset all subscriber/buffer state between tests."""
    with events_mod._lock:
        events_mod._subscribers.clear()
        events_mod._buffers.clear()
        events_mod._buf_counters.clear()
    yield
    with events_mod._lock:
        events_mod._subscribers.clear()
        events_mod._buffers.clear()
        events_mod._buf_counters.clear()


class TestSubscribeUnsubscribe:
    def test_subscribe_creates_queue(self):
        q = events_mod.subscribe("test_ch")
        assert isinstance(q, queue.Queue)
        assert q in events_mod._subscribers["test_ch"]

    def test_unsubscribe_removes_queue(self):
        q = events_mod.subscribe("test_ch")
        events_mod.unsubscribe("test_ch", q)
        assert q not in events_mod._subscribers.get("test_ch", [])

    def test_unsubscribe_nonexistent_is_noop(self):
        q = queue.Queue()
        events_mod.unsubscribe("nope", q)  # should not raise


class TestBroadcast:
    def test_broadcast_reaches_subscribers(self):
        q = events_mod.subscribe("ch1")
        events_mod.broadcast("ch1", {"type": "message", "content": "hi"})
        payload = q.get(timeout=1)
        data = json.loads(payload)
        assert data["type"] == "message"
        assert data["content"] == "hi"

    def test_broadcast_full_queue_does_not_raise(self):
        q = events_mod.subscribe("ch_full")
        # Fill the queue to maxsize (200)
        for _ in range(200):
            q.put_nowait("old")
        # This should not raise even though queue is full
        events_mod.broadcast("ch_full", {"type": "test"})

    def test_broadcast_stores_in_ring_buffer(self):
        events_mod.broadcast("buf_ch", {"n": 1})
        events_mod.broadcast("buf_ch", {"n": 2})
        bufs = events_mod._buffers.get("buf_ch", [])
        assert len(bufs) == 2
        assert bufs[0][1] == json.dumps({"n": 1})
        assert bufs[1][1] == json.dumps({"n": 2})

    def test_ring_buffer_trims(self):
        events_mod._BUFFER_SIZE = 5
        for i in range(10):
            events_mod.broadcast("trim_ch", {"i": i})
        bufs = events_mod._buffers["trim_ch"]
        assert len(bufs) == 5
        assert json.loads(bufs[0][1])["i"] == 5  # first 5 dropped
        events_mod._BUFFER_SIZE = 200  # restore


class TestGetBufferSince:
    def test_returns_payloads_after_cursor(self):
        for i in range(5):
            events_mod.broadcast("gs_ch", {"i": i})
        # Cursor at index 3
        bufs = events_mod._buffers["gs_ch"]
        cursor = bufs[2][0]
        result = events_mod.get_buffer_since("gs_ch", cursor)
        assert len(result) == 2
        assert json.loads(result[0])["i"] == 3
        assert json.loads(result[1])["i"] == 4

    def test_empty_when_cursor_at_end(self):
        events_mod.broadcast("gs2", {"x": 1})
        bufs = events_mod._buffers["gs2"]
        result = events_mod.get_buffer_since("gs2", bufs[-1][0])
        assert result == []


class TestStream:
    def _collect_stream(self, channel, cursor=0, timeout=0.3, max_items=5):
        """Collect stream items in a thread to avoid blocking forever."""
        results = []
        import threading
        gen = [None]
        def _run():
            gen[0] = events_mod.stream(channel, cursor=cursor, timeout=timeout)
            for item in gen[0]:
                results.append(item)
                if len(results) >= max_items:
                    break
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=3)
        if gen[0] is not None:
            gen[0].close()
        return results

    def test_stream_replays_buffered(self):
        events_mod.broadcast("strm", {"a": 1})
        events_mod.broadcast("strm", {"a": 2})
        bufs = events_mod._buffers["strm"]
        cursor = bufs[0][0]  # replay everything after first
        lines = self._collect_stream("strm", cursor=cursor, max_items=3)
        assert lines[0] == ": connected\n\n"
        data_lines = [l for l in lines if l.startswith("data:")]
        assert len(data_lines) >= 1
        assert "a" in data_lines[0]

    def test_stream_yields_keepalive_on_timeout(self):
        lines = self._collect_stream("ka_ch", cursor=0, max_items=3)
        assert any(": keepalive" in l for l in lines)

    def test_stream_cleans_up_subscriber(self):
        self._collect_stream("cleanup_ch", cursor=0, max_items=2)
        assert len(events_mod._subscribers.get("cleanup_ch", [])) == 0
