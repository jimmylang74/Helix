"""Unit tests for the EventSource family (base / registry / channel mount / concrete sources)."""

import time
from unittest import mock

import pytest

from modules.channels.base import ChannelAdapter, ChannelStatus
from modules.channels.cron.scheduler import CronEventSource
from modules.events import (
    EventBus,
    EventSource,
    EventSourceRegistry,
    WechatEvent,
    get_source_registry,
)
from modules.channels.wechat import event_source as wechat_source_mod
from modules.channels.wechat.authenticator import WeChatAuthenticator
from modules.channels.wechat.event_source import WechatEventSource
from modules.channels.wechat.ilink_client import ILinkBotsClient

WAIT_TIMEOUT = 5.0


def _wait_until(pred, timeout=WAIT_TIMEOUT):
    deadline = time.time() + timeout
    while not pred() and time.time() < deadline:
        time.sleep(0.02)
    return pred()


class _FakeBus(EventBus):
    """Captures published events; no consumer threads, no global state."""

    def __init__(self):
        super().__init__()
        self.published = []

    def publish(self, event):
        self.published.append(event)


class _IdleSource(EventSource):
    """Blocks in the loop until told to stop."""

    source_name = "idle"
    thread_name = "idle-thread"

    def _loop(self):
        self._stop_event.wait()


class _BoomSource(EventSource):
    source_name = "boom"
    thread_name = "boom-thread"

    def _loop(self):
        raise RuntimeError("boom")


class _SrcA(EventSource):
    source_name = "a"
    thread_name = "a-thread"

    def _loop(self):
        self._stop_event.wait()


class _SrcB(EventSource):
    source_name = "b"
    thread_name = "b-thread"

    def _loop(self):
        self._stop_event.wait()


class _FakeAuth(WeChatAuthenticator):
    def __init__(self, authenticated=True):
        super().__init__(client=_FakeClient())
        self._authenticated = authenticated


class _FakeClient(ILinkBotsClient):
    def __init__(self):
        super().__init__(bot_token="tok")
        self.responses = []

    def getupdates(self, timeout=35):
        if self.responses:
            return self.responses.pop(0)
        time.sleep(0.05)
        return {"ret": 0, "msgs": []}

    @staticmethod
    def is_token_error(data):
        return (data.get("errcode") or data.get("ret")) in (-14, 40002)


class TestEventSourceBase:
    def test_start_stop_lifecycle(self):
        src = _IdleSource(event_bus=_FakeBus())
        assert not src.is_running
        assert src.start() is True
        assert _wait_until(lambda: src.is_running)
        assert src.start() is False  # idempotent
        assert src.stop() is True
        assert _wait_until(lambda: not src.is_running)
        assert src.stop() is False  # idempotent

    def test_loop_exception_captured_as_last_error(self):
        src = _BoomSource(event_bus=_FakeBus())
        src.start()
        assert _wait_until(lambda: src.get_status()["last_error"] == "boom")
        assert not src.is_running
        assert src.get_status()["status"] == "stopped"
        assert _wait_until(lambda: not src.get_status()["thread_alive"])

    def test_publish_counts_and_stamps(self):
        bus = _FakeBus()
        src = _IdleSource(event_bus=bus)
        src.publish(WechatEvent(sender_id="u1", content="hi"))
        assert bus.published[0].get("content") == "hi"
        assert src.get_status()["events_produced"] == 1
        assert src.get_status()["last_event_at"] is not None
        assert src.get_status()["status"] == "stopped"
        assert src.get_status()["source"] == "idle"

    def test_get_status_keys(self):
        src = _IdleSource(event_bus=_FakeBus())
        status = src.get_status()
        assert set(status) == {
            "source",
            "status",
            "events_produced",
            "last_event_at",
            "last_error",
            "thread_alive",
        }


class TestEventSourceRegistry:
    def test_register_get_get_all_snapshot(self):
        reg = EventSourceRegistry()
        s1, s2 = _IdleSource(), _IdleSource()
        s2.source_name = "idle2"
        assert reg.register(s1) is True
        assert reg.register(s2) is True
        assert reg.get("idle") is s1
        assert reg.get_all() == [s1, s2]
        assert [d["source"] for d in reg.snapshot()] == ["idle", "idle2"]

    def test_duplicate_register_ignored(self):
        reg = EventSourceRegistry()
        s = _IdleSource()
        assert reg.register(s) is True
        assert reg.register(s) is False
        assert len(reg.get_all()) == 1

    def test_empty_source_name_raises(self):
        reg = EventSourceRegistry()
        s = _IdleSource()
        s.source_name = ""
        with pytest.raises(ValueError):
            reg.register(s)

    def test_unregister(self):
        reg = EventSourceRegistry()
        s = _IdleSource()
        reg.register(s)
        assert reg.unregister("idle") is True
        assert reg.get("idle") is None
        assert reg.unregister("idle") is False

    def test_start_all_stop_all(self):
        reg = EventSourceRegistry()
        s1, s2 = _IdleSource(), _IdleSource()
        s2.source_name = "idle2"
        reg.register(s1)
        reg.register(s2)
        reg.start_all()
        assert _wait_until(lambda: s1.is_running and s2.is_running)
        reg.start_all()  # idempotent
        assert s1.is_running and s2.is_running
        reg.stop_all()
        assert _wait_until(lambda: not s1.is_running and not s2.is_running)

    def test_get_source_registry_singleton(self):
        assert get_source_registry() is get_source_registry()


class TestChannelMount:
    def _make_channel(self):
        class MiniChannel(ChannelAdapter):
            def __init__(self):
                super().__init__()
                self._running = False

            @property
            def channel_type(self):
                return "mini"

            @property
            def is_running(self):
                return self._running

            def start(self):
                self._running = True

            def stop(self):
                self._running = False

            def send(self, content, msg_type="text", **kwargs):
                return {"ok": True}

            def get_messages(self, limit=50):
                return []

            def get_status(self):
                return ChannelStatus(
                    channel_type="mini", is_running=self._running, is_authenticated=False
                )

            def restore_session(self):
                return False

            def ask_user(self, request_id, question):
                return ""

            def get_context(self):
                return ""

            def clear_context(self):
                return ""

        return MiniChannel()

    def test_attach_detach_roundtrip(self):
        ch = self._make_channel()
        a = _SrcA()
        assert ch.attach_source(a) is True
        assert ch.attach_source(a) is False  # duplicate by name
        assert ch.attach_source(_SrcA()) is False  # same source_name
        assert ch.event_sources == [a]
        assert ch.detach_source("a") is True
        assert ch.detach_source("a") is False
        assert ch.event_sources == []

    def test_start_stop_sources_drives_attached(self):
        ch = self._make_channel()
        a, b = _SrcA(), _SrcB()
        ch.attach_source(a)
        ch.attach_source(b)
        ch._start_sources()
        assert _wait_until(lambda: a.is_running and b.is_running)
        ch._stop_sources()
        assert _wait_until(lambda: not a.is_running and not b.is_running)


class TestWechatEventSource:
    def _make_source(self, client=None, auth=None, **kwargs):
        return WechatEventSource(
            client or _FakeClient(),
            auth or _FakeAuth(),
            event_bus=_FakeBus(),
            **kwargs,
        )

    def test_start_requires_authenticated(self):
        src = self._make_source(auth=_FakeAuth(authenticated=False))
        with mock.patch.object(wechat_source_mod, "update_session_status") as upd:
            assert src.start() is False
            assert not src.is_running
            upd.assert_not_called()

    def test_poll_processes_message_end_to_end(self):
        client = _FakeClient()
        msg = {
            "msg_id": "m1",
            "from_user_id": "u1",
            "from_user_name": "Alice",
            "context_token": "ctx-1",
            "message_type": 1,
            "create_time_ms": 1700000000000,
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
        }
        client.responses.append({"ret": 0, "msgs": [msg]})
        bus = _FakeBus()
        src = WechatEventSource(client, _FakeAuth(), poll_timeout=5, event_bus=bus)
        with mock.patch.object(
            wechat_source_mod, "save_message"
        ) as save_msg, mock.patch.object(
            wechat_source_mod, "update_session_status"
        ) as upd, mock.patch(
            "modules.channels.events.broadcast"
        ) as broadcast, mock.patch.object(
            wechat_source_mod, "parse_media_item", return_value=None
        ):
            assert src.start() is True
            try:
                # publish 是 _handle_update 的最后一步：观察到发布即表明
                # save_message / broadcast 均已执行完毕（避免中间态竞态）
                assert _wait_until(lambda: len(bus.published) == 1)
                assert src.last_from_user_id == "u1"
                ev = bus.published[0]
                assert isinstance(ev, WechatEvent)
                assert ev.get("sender_id") == "u1"
                assert ev.get("sender_name") == "Alice"
                assert ev.get("content") == "hello"
                assert ev.get("context_token") == "ctx-1"

                save_msg.assert_called_once()
                kw = save_msg.call_args.kwargs
                assert kw["sender_id"] == "u1"
                assert kw["direction"] == "incoming"
                assert kw["content"] == "hello"
                assert kw["msg_type"] == "text"

                broadcast.assert_called_once()
                payload = broadcast.call_args.args[1]
                assert payload["type"] == "message"
                assert payload["direction"] == "incoming"
                assert payload["content"] == "hello"

                upd.assert_called_once_with("wechat", "connected")
                assert src.get_status()["events_produced"] == 1
                assert src.get_status()["last_event_at"] is not None
            finally:
                src.stop()

    def test_token_error_stops_loop_and_clears_auth(self):
        client = _FakeClient()
        client.responses.append({"errcode": -14, "errmsg": "invalid token"})
        auth = _FakeAuth()
        src = self._make_source(client=client, auth=auth)
        with mock.patch.object(wechat_source_mod, "update_session_status") as upd:
            src.start()
            assert _wait_until(lambda: not src.is_running)
            assert (
                src.last_error
                == "bot_token invalid (errcode -14), please re-scan QR code"
            )
            assert auth._authenticated is False
            assert client.bot_token == ""
            assert upd.call_args.args == ("wechat", "token_expired")
            status = src.get_status()
            assert status["status"] == "stopped"
            assert status["last_error"] == src.last_error

    def test_non_token_errcode_keeps_polling(self):
        client = _FakeClient()
        client.responses.append({"errcode": 40000, "errmsg": "server busy", "msgs": []})
        src = self._make_source(client=client)
        src.start()
        try:
            time.sleep(0.3)
            assert src.is_running
            assert src.get_status()["last_error"] is None
        finally:
            src.stop()

    def test_poll_timeout_clamped_and_roundtrip(self):
        src = self._make_source(poll_timeout=5)
        assert src.poll_timeout == 5
        src.poll_timeout = 1
        assert src.poll_timeout == 5  # clamped to minimum
        src.poll_timeout = 60
        assert src.poll_timeout == 60

    def test_status_before_start(self):
        src = self._make_source()
        status = src.get_status()
        assert status["source"] == "wechat"
        assert status["status"] == "stopped"
        assert status["thread_alive"] is False


class TestCronEventSource:
    def test_get_status_shape(self):
        cron = CronEventSource()
        status = cron.get_status()
        assert status["source"] == "cron"
        assert status["status"] in ("started", "stopped")
        assert isinstance(status["task_count"], int)
        assert isinstance(status["enabled_count"], int)
        assert status["next_run"] is None or isinstance(status["next_run"], str)
        assert status["error"] == (status.get("last_error") or "")

    def test_start_stop_idempotent_and_is_started_alias(self):
        cron = CronEventSource()
        assert not cron.is_started
        assert cron.start() is True
        assert _wait_until(lambda: cron.is_started)
        assert cron.start() is False
        assert cron.stop() is True
        assert _wait_until(lambda: not cron.is_started)
        assert cron.stop() is False

    def test_registry_drives_cron_source(self):
        reg = EventSourceRegistry()
        cron = CronEventSource()
        reg.register(cron)
        reg.start_all()
        assert _wait_until(lambda: cron.is_started)
        reg.stop_all()
        assert _wait_until(lambda: not cron.is_started)