"""Unit tests for modules.channels.dispatcher (OutputDispatcher)."""

import pytest

from modules.channels.dispatcher import OutputDispatcher, get_dispatcher


class FakeSink:
    """按 ChannelAdapter.send 契约返回 dict 的假 sink，可注入失败行为。"""

    def __init__(self, display_name="FakeSink", error=None, exc=None):
        self.display_name = display_name
        self.error = error
        self.exc = exc
        self.calls = []

    def send(self, content, **kwargs):
        self.calls.append((content, kwargs))
        if self.exc is not None:
            raise self.exc
        if self.error is not None:
            return {"error": self.error}
        return {"ok": True}


class TestRegisterAndQuery:
    def test_register_and_available(self):
        d = OutputDispatcher()
        d.register("ilinkbot", FakeSink(display_name="iLinkBot"))
        d.register("discord", FakeSink())
        items = d.available()
        assert [i["id"] for i in items] == ["discord", "ilinkbot"]
        labels = {i["id"]: i["label"] for i in items}
        assert labels["ilinkbot"] == "iLinkBot"
        assert labels["discord"] == "FakeSink"  # 无 label 时回退 display_name

    def test_register_label_fallback_to_channel_id(self):
        d = OutputDispatcher()
        d.register("plain", object())
        items = d.available()
        assert items[0]["label"] == "plain"

    def test_register_normalizes_and_requires_args(self):
        d = OutputDispatcher()
        d.register("  iLinkBot ", FakeSink(), label="X")
        assert d.is_registered("ilinkbot")
        assert d.is_registered("  ILINKBOT  ")
        with pytest.raises(ValueError):
            d.register("", FakeSink())
        with pytest.raises(ValueError):
            d.register("ok", None)

    def test_unregister(self):
        d = OutputDispatcher()
        d.register("a", FakeSink())
        assert d.unregister("A") is True
        assert d.is_registered("a") is False
        assert d.unregister("a") is False
        assert d.available() == []


class TestSend:
    def test_send_success_returns_detail(self):
        d = OutputDispatcher()
        sink = FakeSink()
        d.register("ilinkbot", sink)
        out = d.send("ilinkbot", "hello", to_user_id="u1")
        assert out["ok"] is True
        assert out["detail"] == {"ok": True}
        assert sink.calls == [("hello", {"to_user_id": "u1"})]

    def test_send_unknown_channel(self):
        d = OutputDispatcher()
        # 注册表为空时任何 id 都是未注册，返回错误 dict 而非抛异常
        out = d.send("nope", "hello")
        assert out["ok"] is False
        assert "未注册" in out["error"]

    def test_send_sink_error_dict(self):
        d = OutputDispatcher()
        d.register("discord", FakeSink(error="rate limited"))
        out = d.send("discord", "hello")
        assert out["ok"] is False
        assert out["error"] == "rate limited"

    def test_send_sink_exception_is_caught(self):
        d = OutputDispatcher()
        d.register("boom", FakeSink(exc=RuntimeError("down")))
        out = d.send("boom", "hello")
        assert out["ok"] is False
        assert "RuntimeError" in out["error"]

    def test_send_channel_id_normalized(self):
        d = OutputDispatcher()
        sink = FakeSink()
        d.register("ilinkbot", sink)
        out = d.send("  ILINKBOT ", "hi")
        assert out["ok"] is True


class TestSingleton:
    def test_get_dispatcher_is_process_singleton(self):
        d1 = get_dispatcher()
        d2 = get_dispatcher()
        assert d1 is d2