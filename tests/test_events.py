"""Unit tests for modules.events (EventBase subclasses + EventBus)."""

import threading
import time

import pytest

from modules.events import (
    EventBase,
    EventBus,
    TimerEvent,
    WechatEvent,
    get_event_bus,
)


class TestEventBase:
    def test_event_id_unique(self):
        assert EventBase().event_id != EventBase().event_id

    def test_subclass_route_fields(self):
        te = TimerEvent(task={"id": "t1"})
        assert te.event_type == "cron.timer"
        assert te.source == "cron"
        assert te.get("task") == {"id": "t1"}

        we = WechatEvent(sender_id="u1", content="hi")
        assert we.event_type == "wechat.message"
        assert we.source == "wechat"
        assert we.get("sender_id") == "u1"
        assert we.get("content") == "hi"

    def test_payload_defaults_and_get(self):
        we = WechatEvent()
        assert we.get("sender_id") == ""
        assert we.get("missing", "dflt") == "dflt"

    def test_occurred_at_stamped_at_construction(self):
        before = time.time() - 0.1
        ev = TimerEvent()
        after = time.time() + 0.1
        assert before <= ev.occurred_at <= after
        assert ev.received_at == 0.0


class _Collector:
    """记录收到事件的订阅者 callable。"""

    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)


def _wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while not pred() and time.time() < deadline:
        time.sleep(0.02)
    return pred()


class TestEventBus:
    def test_delivers_to_subscriber_in_order(self):
        bus = EventBus()
        col = _Collector()
        bus.subscribe(col)
        try:
            for i in range(5):
                bus.publish(TimerEvent(task={"id": f"t{i}"}))
            assert _wait_until(lambda: len(col.events) == 5)
            assert [ev.get("task")["id"] for ev in col.events] == [
                f"t{i}" for i in range(5)
            ]
        finally:
            bus.stop()

    def test_publish_stamps_received_at(self):
        bus = EventBus()
        col = _Collector()
        bus.subscribe(col)
        try:
            ev = TimerEvent()
            assert ev.received_at == 0.0
            bus.publish(ev)
            assert _wait_until(lambda: len(col.events) == 1)
            assert col.events[0].received_at > 0.0
        finally:
            bus.stop()

    def test_multiple_subscribers_all_receive(self):
        bus = EventBus()
        a, b = _Collector(), _Collector()
        bus.subscribe(a)
        bus.subscribe(b)
        try:
            bus.publish(TimerEvent())
            assert _wait_until(lambda: len(a.events) == 1 and len(b.events) == 1)
        finally:
            bus.stop()

    def test_duplicate_subscribe_ignored(self):
        bus = EventBus()
        col = _Collector()
        bus.subscribe(col)
        bus.subscribe(col)
        try:
            bus.publish(TimerEvent())
            assert _wait_until(lambda: len(col.events) == 1)
            time.sleep(0.05)
            assert len(col.events) == 1  # 只投递一次
        finally:
            bus.stop()

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        col = _Collector()
        bus.subscribe(col)
        bus.publish(TimerEvent())
        assert _wait_until(lambda: len(col.events) == 1)
        assert bus.unsubscribe(col) is True
        bus.publish(TimerEvent())
        time.sleep(0.1)
        assert len(col.events) == 1
        bus.stop()
        assert bus.unsubscribe(col) is False

    def test_subscribe_after_stop_raises(self):
        bus = EventBus()
        bus.subscribe(_Collector())
        bus.stop()
        with pytest.raises(RuntimeError):
            bus.subscribe(_Collector())

    def test_publish_after_stop_dropped(self):
        bus = EventBus()
        col = _Collector()
        bus.subscribe(col)
        bus.stop()
        bus.publish(TimerEvent())
        time.sleep(0.1)
        assert col.events == []

    def test_concurrent_publish_thread_safe(self):
        bus = EventBus()
        col = _Collector()
        bus.subscribe(col)
        n_threads, per_thread = 4, 25
        try:
            def worker():
                for _ in range(per_thread):
                    bus.publish(TimerEvent())

            threads = [threading.Thread(target=worker) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert _wait_until(lambda: len(col.events) == n_threads * per_thread)
        finally:
            bus.stop()

    def test_snapshot_counts_and_recent(self):
        bus = EventBus()
        bus.subscribe(_Collector())
        bus.publish(TimerEvent())
        _wait_until(lambda: bus.snapshot()["total_published"] == 1)
        snap = bus.snapshot()
        assert snap["subscribers"] == 1
        assert snap["total_published"] == 1
        assert len(snap["recent"]) == 1
        assert snap["recent"][0]["event_type"] == "cron.timer"
        bus.stop()
        assert bus.snapshot()["stopped"] is True

    def test_get_event_bus_singleton(self):
        assert get_event_bus() is get_event_bus()