"""Unit tests for modules.events.broker (EventBroker routing)."""

import time

import pytest

from modules.events import (
    EventBroker,
    EventBus,
    TimerEvent,
    WechatEvent,
    get_event_broker,
)


class FakeProcessor:
    """具备 handle_event 契约的假处理器。"""

    def __init__(self):
        self.handled = []

    def handle_event(self, event):
        self.handled.append(event)


class _BoomProcessor:
    """handler 内部抛出异常的假处理器。"""

    def handle_event(self, event):
        raise RuntimeError("boom")


def _wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while not pred() and time.time() < deadline:
        time.sleep(0.02)
    return pred()


class TestEventBroker:
    def test_register_and_dispatch(self):
        broker = EventBroker()
        proc = FakeProcessor()
        broker.register("cron.timer", proc)
        ev = TimerEvent()
        broker.dispatch(ev)
        assert proc.handled == [ev]

    def test_dispatch_classifies_by_event_type(self):
        broker = EventBroker()
        cron_proc, wx_proc = FakeProcessor(), FakeProcessor()
        broker.register("cron.timer", cron_proc)
        broker.register("wechat.message", wx_proc)
        broker.dispatch(TimerEvent())
        broker.dispatch(WechatEvent(sender_id="u1"))
        assert len(cron_proc.handled) == 1
        assert len(wx_proc.handled) == 1

    def test_unregistered_type_dropped_without_error(self):
        broker = EventBroker()
        proc = FakeProcessor()
        broker.register("cron.timer", proc)
        before = broker.snapshot()["dropped"]
        broker.dispatch(WechatEvent())
        assert broker.snapshot()["dropped"] == before + 1
        assert proc.handled == []

    def test_handler_exception_isolated(self):
        broker = EventBroker()
        good = FakeProcessor()
        broker.register("cron.timer", _BoomProcessor())
        broker.register("wechat.message", good)
        broker.dispatch(TimerEvent())     # 处理器抛异常 → 不外抛
        broker.dispatch(WechatEvent())    # 后续事件照常处理
        assert len(good.handled) == 1
        assert broker.snapshot()["failed"] == 1

    def test_register_validates_inputs(self):
        broker = EventBroker()
        with pytest.raises(ValueError):
            broker.register("", FakeProcessor())
        with pytest.raises(TypeError):
            broker.register("x.timer", object())  # 无 handle_event

    def test_unregister(self):
        broker = EventBroker()
        broker.register("cron.timer", FakeProcessor())
        assert broker.unregister("cron.timer") is True
        assert broker.unregister("cron.timer") is False

    def test_route_lookup(self):
        broker = EventBroker()
        proc = FakeProcessor()
        broker.register("cron.timer", proc)
        assert broker.route("cron.timer") is proc
        assert broker.route("nope") is None

    def test_bus_broker_processor_end_to_end(self):
        bus = EventBus()
        broker = EventBroker()
        proc = FakeProcessor()
        broker.register("wechat.message", proc)
        bus.subscribe(broker)
        try:
            bus.publish(WechatEvent(sender_id="u1", content="hi"))
            assert _wait_until(lambda: len(proc.handled) == 1)
            assert proc.handled[0].get("content") == "hi"
        finally:
            bus.stop()

    def test_snapshot_routes(self):
        broker = EventBroker()
        broker.register("cron.timer", FakeProcessor())
        routes = broker.snapshot()["routes"]
        assert routes == {"cron.timer": "FakeProcessor"}

    def test_get_event_broker_singleton(self):
        assert get_event_broker() is get_event_broker()