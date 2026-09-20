"""Unit tests for the ThinkingChannel (concurrency gate / processing / event handling)."""

import os
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from HelixCore.prompts.thinking_prompts import THINKING_INTENT_ID
from modules.channels.dispatcher import get_dispatcher
from modules.channels.thinking import store
from modules.channels.thinking.channel import (
    ThinkingChannel,
    _ConcurrencyGate,
    format_result_message,
)
from modules.channels.thinking.store import get_events
from modules.events import EventSource
from modules.events.base import RssEvent, TimerEvent, WebhookEvent

# Redirect storage paths to a temp dir before touching the store
_tmpdir = tempfile.mkdtemp()

WAIT_TIMEOUT = 5.0


def _wait_until(pred, timeout=WAIT_TIMEOUT):
    deadline = time.time() + timeout
    while not pred() and time.time() < deadline:
        time.sleep(0.02)
    return pred()


@pytest.fixture(autouse=True, scope="session")
def _patch_paths():
    import modules.channels.thinking.store as store_mod
    store_mod._config_path_cache = os.path.join(_tmpdir, "thinking.json")
    store_mod._events_db_path_cache = os.path.join(_tmpdir, "thinking.db")
    yield
    store_mod._config_path_cache = None
    store_mod._events_db_path_cache = None


@pytest.fixture(autouse=True)
def _clean_storage():
    import modules.channels.thinking.store as store_mod
    db_path = store_mod._events_db_path()
    for path in (
        store_mod._config_path(),
        db_path,
        db_path + "-wal",
        db_path + "-shm",
    ):
        if os.path.exists(path):
            os.remove(path)
    yield


class _FakeOrchestrator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def process_request(self, prompt, request_id, forced_intent="auto", **kwargs):
        self.calls.append((prompt, request_id, forced_intent, kwargs))
        return self.result


class _Sink:
    def __init__(self):
        self.messages = []

    def send(self, content, **kwargs):
        self.messages.append(content)
        return {"ok": True}


# ── _ConcurrencyGate ───────────────────────────────────────────────────

class TestConcurrencyGate:
    def test_cap_zero_negative_none_bypassed(self):
        gate = _ConcurrencyGate()
        assert gate.acquire(0) is None
        assert gate.acquire(-1) is None
        assert gate.acquire(None) is None

    def test_acquire_blocks_when_saturated(self):
        gate = _ConcurrencyGate()
        sem = gate.acquire(1)
        assert sem is not None
        entered = threading.Event()

        def worker():
            s = gate.acquire(1)
            assert s is not None
            entered.set()
            s.release()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert entered.wait(0.2) is False  # 容量耗尽 → 阻塞
        sem.release()
        assert entered.wait(WAIT_TIMEOUT) is True
        t.join(timeout=WAIT_TIMEOUT)

    def test_cap_change_rebuilds_semaphore(self):
        gate = _ConcurrencyGate()
        s1 = gate.acquire(1)
        assert s1 is not None
        s1.release()
        s2a = gate.acquire(2)
        s2b = gate.acquire(2)  # 容量 2 → 第二个也能立即拿到
        assert s2a is not None and s2b is not None
        assert s2a is not s1
        s2a.release()
        s2b.release()


# ── format_result_message ──────────────────────────────────────────────

def _record(**over):
    base = {
        "event_id": "evt_x", "event_type": "webhook.push", "title": "标题",
        "content": "内容", "status": "success",
        "started_at": "2026-09-20 10:00:00", "finished_at": "2026-09-20 10:00:01",
        "duration_ms": 1000, "output": "结果输出", "error": "",
    }
    base.update(over)
    return base


class TestFormatResultMessage:
    def test_success_record(self):
        msg = format_result_message(_record())
        assert "【思考事件处理结果】" in msg
        assert "成功" in msg
        assert "标题" in msg
        assert "evt_x" in msg
        assert "结果输出" in msg
        assert "1000 ms" in msg

    def test_failed_record_includes_error(self):
        msg = format_result_message(_record(status="failed", error="boom"))
        assert "失败" in msg
        assert "[错误] boom" in msg

    def test_long_output_truncated(self):
        msg = format_result_message(_record(output="x" * 6000))
        assert "（输出过长，已截断）" in msg
        assert len(msg) < 6000


# ── 通道基本契约 ───────────────────────────────────────────────────────

class _IdleSource(EventSource):
    """事件驱动型（线程跑 _loop）的最小事件源替身。"""

    source_name = "idle"

    def __init__(self, name):
        super().__init__()
        self.source_name = name

    def _loop(self):
        self._stop_event.wait()

    def get_status(self):
        return {
            "status": "running" if self.is_running else "stopped",
            "last_error": None,
        }


class TestChannelBasics:
    def test_channel_type_and_message_surface(self):
        ch = ThinkingChannel()
        assert ch.channel_type == "thinking"
        assert ch.send("hi") == {"channel": "thinking", "delivered": False}
        assert ch.get_messages() == []
        assert ch.restore_session() is True
        assert ch.is_running is False                      # 无挂载源
        assert ch.get_status().is_running is False

    def test_channel_tool_fallbacks(self):
        ch = ThinkingChannel()
        assert "无法向用户提问" in ch.ask_user("req1", "问题?")
        assert "没有历史上下文" in ch.get_context()
        assert "无需清除上下文" in ch.clear_context()

    def test_start_stop_drives_attached_sources(self):
        ch = ThinkingChannel()
        s1, s2 = _IdleSource("a"), _IdleSource("b")
        ch.attach_source(s1)
        ch.attach_source(s2)
        ch.start()
        assert ch.is_running is True
        ch.stop()
        assert ch.is_running is False

    def test_unsupported_event_type_ignored(self):
        ch = ThinkingChannel()
        ch.handle_event(TimerEvent(task={"title": "定时任务"}))
        assert get_events() == []


# ── _process（私有编排器调用） ─────────────────────────────────────────

class TestProcess:
    def test_no_runtime_returns_failed(self):
        ch = ThinkingChannel()
        ch.runtime = None
        out = ch._process(WebhookEvent(origin="x", content="hello", raw={}))
        assert out[0] == "failed"
        assert "尚未装配" in out[2]

    def test_empty_prompt_returns_failed(self):
        ch = ThinkingChannel()
        orch = _FakeOrchestrator({"final_result": "", "error": None})
        ch.runtime = SimpleNamespace(orchestrator=orch)
        out = ch._process(WebhookEvent(origin="x", content="   ", raw={}))
        assert out[0] == "failed"
        assert "事件内容为空" in out[2]
        assert orch.calls == []

    def test_success_calls_orchestrator_with_thinking_intent(self):
        ch = ThinkingChannel()
        orch = _FakeOrchestrator({"final_result": "分析完成", "error": None})
        ch.runtime = SimpleNamespace(orchestrator=orch)
        ev = WebhookEvent(origin="jenkins", content="做季度总结", raw={})
        out = ch._process(ev)
        assert out == ("success", "分析完成", "")
        prompt, request_id, intent, kwargs = orch.calls[0]
        assert prompt == "做季度总结"
        assert request_id.startswith("req_")
        assert intent == THINKING_INTENT_ID
        assert "context_injections" in kwargs

    def test_errored_result_maps_to_failed(self):
        ch = ThinkingChannel()
        orch = _FakeOrchestrator({"final_result": "部分结果", "error": "LLM down"})
        ch.runtime = SimpleNamespace(orchestrator=orch)
        out = ch._process(WebhookEvent(origin="x", content="hi", raw={}))
        assert out == ("failed", "部分结果", "LLM down")

    def test_crashed_orchestrator_propagates_to_caller(self):
        # _process 不吞异常（由 _handle_event_locked 兜底落 failed），此处验证传播
        ch = ThinkingChannel()

        class Boom:
            def process_request(self, *a, **k):
                raise RuntimeError("boom")

        ch.runtime = SimpleNamespace(orchestrator=Boom())
        with pytest.raises(RuntimeError):
            ch._process(WebhookEvent(origin="x", content="hi", raw={}))


# ── 事件内容组装 ───────────────────────────────────────────────────────

class TestBuildPromptAndTitle:
    def test_build_prompt_rss(self):
        ch = ThinkingChannel()
        ev = RssEvent(
            feed_url="https://a.com/feed", entry_id="e1",
            entry_title="标题", entry_link="https://a.com/p1",
            entry_summary="摘要",
        )
        prompt = ch._build_prompt(ev)
        assert "标题: 标题" in prompt
        assert "链接: https://a.com/p1" in prompt
        assert "摘要: 摘要" in prompt

    def test_build_prompt_webhook(self):
        ch = ThinkingChannel()
        ev = WebhookEvent(origin="x", content="hello", raw={})
        assert ch._build_prompt(ev) == "hello"

    def test_record_title_rss_and_webhook(self):
        ch = ThinkingChannel()
        rss = RssEvent(feed_url="u", entry_id="e", entry_title="New entry")
        assert ch._record_title(rss) == "New entry"
        wh = WebhookEvent(origin="jenkins", content="x", raw={})
        assert ch._record_title(wh) == "WebHook 推送（jenkins）"
        wh2 = WebhookEvent(origin="", content="x", raw={})
        assert ch._record_title(wh2) == "WebHook 推送"


# ── handle_event 端到端（落库 + 输出通道推送） ─────────────────────────

class TestHandleEvent:
    def _channel_with_fake_runtime(self, result=None, error=None):
        ch = ThinkingChannel()
        orch = _FakeOrchestrator(
            {"final_result": result or "处理结果", "error": error}
        )
        ch.runtime = SimpleNamespace(orchestrator=orch)
        return ch, orch

    def test_webhook_event_saved_success(self):
        ch, orch = self._channel_with_fake_runtime(result="季度总结完成")
        ch.handle_event(WebhookEvent(origin="jenkins", content="做季度总结", raw={}))
        assert _wait_until(lambda: len(get_events()) == 1)
        record = get_events()[0]
        assert record["event_type"] == "webhook.push"
        assert record["status"] == "success"
        assert record["title"] == "WebHook 推送（jenkins）"
        assert record["content"] == "做季度总结"
        assert record["output"] == "季度总结完成"
        assert record["event_id"].startswith("evt_")
        assert orch.calls and orch.calls[0][2] == THINKING_INTENT_ID

    def test_rss_event_saved(self):
        ch, _ = self._channel_with_fake_runtime()
        ch.handle_event(
            RssEvent(
                feed_url="https://a.com", entry_id="e1",
                entry_title="RSS 标题", entry_link="https://a.com/p",
                entry_summary="摘要文字",
            )
        )
        assert _wait_until(lambda: len(get_events()) == 1)
        record = get_events()[0]
        assert record["event_type"] == "rss.feed"
        assert record["status"] == "success"
        assert record["title"] == "RSS 标题"
        assert record["content"] == "标题: RSS 标题\n链接: https://a.com/p\n摘要: 摘要文字"

    def test_crashed_processing_saved_as_failed(self):
        ch = ThinkingChannel()

        class Boom:
            def process_request(self, *a, **k):
                raise RuntimeError("LLM crashed")

        ch.runtime = SimpleNamespace(orchestrator=Boom())
        ch.handle_event(WebhookEvent(origin="x", content="hi", raw={}))
        assert _wait_until(lambda: len(get_events()) == 1)
        record = get_events()[0]
        assert record["status"] == "failed"
        assert "RuntimeError" in record["error"]

    def test_result_pushed_to_output_channels(self):
        sink = _Sink()
        try:
            get_dispatcher().register("tpush", sink)
            store.save_config({"output_channels": ["tpush"]})
            ch, _ = self._channel_with_fake_runtime()
            ch.handle_event(WebhookEvent(origin="x", content="hello", raw={}))
            assert _wait_until(lambda: len(get_events()) == 1)
            assert _wait_until(lambda: len(sink.messages) == 1)
            assert "【思考事件处理结果】" in sink.messages[0]
            assert "WebHook 推送（x）" in sink.messages[0]  # 内容行展示记录标题
            assert "处理结果" in sink.messages[0]
        finally:
            get_dispatcher().unregister("tpush")