"""
ThinkingChannel — Helix 独立"思考"通道（外部事件驱动）。

区别于 IM 通道：无外部消息接入，也不承载用户会话；事件（RSS 新条目 /
WebHook 推送）经 EventBroker 路由到 handle_event()，通道以固定 Thinking 意图
（forced_intent=thinking，独立 LLM 日志 llm_engine_thinking.log）交给私有
agent 运行时处理，结果落 db/thinking.db，并按配置推送到输出通道。装配要点：

- start(): ensure_config() 初始化 db/thinking.json 后随 _start_sources() 拉起
  已挂载事件源（RssEventSource / WebhookEventSource —— 组合根 attach_source）
- handle_event(): EventBroker 分发落点 —— 为每个事件起独立 worker 线程执行，
  经并发门卫（db/thinking.json 的 concurrency，0=不限制）限流同时处理数
- _process(): 组装事件内容为提示词，以 forced_intent=THINKING_INTENT_ID 调用
  本通道私有编排器（context_injections 注入 build_injections()，与 Web 快速
  测试通道的 thinking 分支同构），结果落 db/thinking.db
- stop()/is_running: 跟随已挂载事件源的运行状态（任一未启动即视为停止，
  使 ChannelManager.stop_all 生效）
- send(): 无推送出口 —— 事件处理结果统一落 db/thinking.db，前端思考通道页
  查看；"输出通道"推送由 _run_event 直接经 OutputDispatcher 完成
- ask_user/get_context/clear_context: 本通道装配时不注册三件套工具
  （build_channel_runtime(include_channel_tools=False)），这些落点
  正常情况下不会被触达；保留兜底返回提示文本。
"""

import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from HelixCore.prompts.thinking_prompts import THINKING_INTENT_ID
from modules.channels.base import ChannelAdapter, ChannelMessage, ChannelStatus
from modules.channels.thinking import store
from modules.events.base import EventBase
from modules.host.helix_profile import build_injections
from modules.utils.logger import log_debug, log_error, log_info, log_tool_call, log_warning

# 本通道可处理的事件类型（EventBroker 路由键）
THINKING_EVENT_TYPES = ("rss.feed", "webhook.push")


def format_result_message(record: Dict[str, Any], max_output: int = 5000) -> str:
    """把一条思考事件处理记录格式化为适合 IM 推送的纯文本消息。"""
    status = "成功" if record["status"] == "success" else "失败"
    lines = [
        "【思考事件处理结果】",
        f"内容: {record['title']}",
        f"记录ID: {record['event_id']}",
        f"事件类型: {record['event_type']}",
        f"状态: {status}",
        f"开始: {record['started_at']}",
        f"结束: {record['finished_at']}",
        f"耗时: {record['duration_ms']} ms",
    ]
    output = (record.get("output") or "").strip()
    if output:
        if len(output) > max_output:
            output = output[:max_output] + "\n…（输出过长，已截断）"
        lines.append("─────────────────")
        lines.append(output)
    error = (record.get("error") or "").strip()
    if error:
        lines.append("─────────────────")
        lines.append(f"[错误] {error}")
    return "\n".join(lines)


class _ConcurrencyGate:
    """按配置并发上限限流的信号量门卫（上限可热更新，变化后按新上限重建）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sem: Optional[threading.BoundedSemaphore] = None
        self._cap: Optional[int] = None

    def acquire(self, cap: Optional[int]) -> Optional[threading.BoundedSemaphore]:
        """获取并发额度；cap<=0（不限制）返回 None 表示直接放行。

        返回非 None 时调用方必须在 finally 中 release()。
        """
        if cap is None or cap <= 0:
            return None
        with self._lock:
            if self._sem is None or self._cap != cap:
                self._sem = threading.BoundedSemaphore(cap)
                self._cap = cap
            sem = self._sem
        sem.acquire()
        return sem


class ThinkingChannel(ChannelAdapter):
    """思考通道 — 消费 rss.feed / webhook.push 事件并固定以 Thinking 意图处理。"""

    CHANNEL_TYPE = "thinking"

    def __init__(self) -> None:
        super().__init__()
        self._gate = _ConcurrencyGate()

    # ── Lifecycle ──────────────────────────────────────────────────────

    @property
    def channel_type(self) -> str:
        return self.CHANNEL_TYPE

    @property
    def is_running(self) -> bool:
        """跟随已挂载事件源运行状态（全部运行才是真正运行）。"""
        return bool(self.event_sources) and all(
            src.is_running for src in self.event_sources
        )

    def start(self) -> None:
        """初始化配置并启动全部挂载事件源（幂等）。"""
        store.ensure_config()
        self._start_sources()

    def stop(self) -> None:
        """停止全部挂载事件源（幂等）。"""
        self._stop_sources()

    def restore_session(self) -> bool:
        """无持久会话；实际启动由组合根显式调用 start() 完成。"""
        return True

    # ── Messaging ──────────────────────────────────────────────────────

    def send(self, content: str, msg_type: str = "text", **kwargs) -> Dict[str, Any]:
        """无独立推送出口 — 事件结果经 store.save_event 落 db/thinking.db。"""
        return {"channel": self.CHANNEL_TYPE, "delivered": False}

    def get_messages(self, limit: int = 50) -> List[ChannelMessage]:
        return []

    def get_status(self) -> ChannelStatus:
        rss_src = next(
            (s for s in self.event_sources if s.source_name == "rss"), None
        )
        webhook_src = next(
            (s for s in self.event_sources if s.source_name == "webhook"), None
        )
        error = None
        for src in self.event_sources:
            err = src.get_status().get("last_error")
            if err:
                error = err
                break
        cfg = store.load_config()
        return ChannelStatus(
            channel_type=self.CHANNEL_TYPE,
            is_running=self.is_running,
            is_authenticated=True,
            display_name="思考通道",
            error=error,
            extra={
                "concurrency": cfg.get("concurrency", 0),
                "rss_feeds": len(cfg["rss"]["feeds"]),
                "rss_status": rss_src.get_status()["status"] if rss_src else "stopped",
                "webhook_status": (
                    webhook_src.get_status()["status"] if webhook_src else "stopped"
                ),
            },
        )

    # ── 通道工具落点（本通道不注册三件套工具，以下仅为兜底）────────────

    def ask_user(self, request_id: str, question: str) -> str:
        log_debug("[thinking] ask_user 被调用 — 思考通道为自动处理，无法向用户提问")
        return "错误: 思考通道为外部事件自动处理，无法向用户提问，请基于已有信息继续"

    def get_context(self) -> str:
        log_debug("[thinking] get_context 被调用 — 思考通道无会话上下文")
        return "思考通道为一次性事件处理，没有历史上下文"

    def clear_context(self) -> str:
        log_debug("[thinking] clear_context 被调用 — 思考通道无会话可清除")
        return "思考通道为一次性事件处理，无需清除上下文"

    # ── 外部事件处理器（EventBroker 路由落点）─────────────────────────

    def handle_event(self, event: EventBase) -> None:
        """EventBroker 分发入口：为每个事件起独立 worker 线程执行（并发受限）。"""
        if event.event_type not in THINKING_EVENT_TYPES:
            log_warning(f"[thinking] 忽略不支持的事件类型: {event.event_type}")
            return
        record_id = store.new_event_id()
        cap = int(store.load_config().get("concurrency", 0) or 0)
        threading.Thread(
            target=self._run_event,
            args=(event, record_id, cap),
            daemon=True,
            name=f"thinking-{event.event_id[:8]}",
        ).start()

    # ── 事件处理（由事件源发布迁移至此）───────────────────────────────

    def _run_event(self, event: EventBase, record_id: str, cap: int) -> None:
        """worker：经并发门卫限流后执行单个事件处理并把结果写入 db/thinking.db。"""
        sem = self._gate.acquire(cap)
        try:
            self._handle_event_locked(event, record_id)
        finally:
            if sem is not None:
                sem.release()

    def _handle_event_locked(self, event: EventBase, record_id: str) -> None:
        started_at = datetime.now()
        status, output, error = "success", "", ""
        try:
            status, output, error = self._process(event)
        except Exception as e:  # 兜底：任何异常都落一条 failed 记录
            status, error = "failed", f"{type(e).__name__}: {e}"
            log_error(f"[thinking] Event {event.event_id} crashed: {e}")
        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        record = store.save_event(
            event_id=record_id,
            event_type=event.event_type,
            title=self._record_title(event),
            content=self._build_prompt(event)[:4000],
            status=status,
            started_at=started_at.strftime("%Y-%m-%d %H:%M:%S"),
            finished_at=finished_at.strftime("%Y-%m-%d %H:%M:%S"),
            duration_ms=duration_ms,
            output=output,
            error=error,
        )
        log_tool_call(
            f"[thinking] {event.event_id} ({event.event_type}) → {status} "
            f"({duration_ms}ms, record={record_id})"
        )

        # 输出通道推送（尽力而为，失败仅记日志，不影响结果落库）
        channels = store.load_config().get("output_channels") or []
        if channels:
            from modules.channels.dispatcher import get_dispatcher

            message = format_result_message(record)
            for ch in channels:
                outcome = get_dispatcher().send(ch, message)
                if outcome.get("ok"):
                    log_info(
                        f"[thinking] Result pushed to output channel "
                        f"'{ch}' ({record_id})"
                    )
                else:
                    log_warning(
                        f"[thinking] Push to output channel '{ch}' failed "
                        f"for {record_id}: {outcome.get('error')}"
                    )

    def _process(self, event: EventBase):
        """把事件内容交给本通道私有编排器（强制 Thinking 意图）执行。"""
        runtime = self.runtime
        if runtime is None or runtime.orchestrator is None:
            return "failed", "", "思考通道运行时尚未装配，无法处理事件"
        prompt = self._build_prompt(event)
        if not prompt.strip():
            return "failed", "", "事件内容为空，无法处理"
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        result = runtime.orchestrator.process_request(
            prompt,
            request_id,
            forced_intent=THINKING_INTENT_ID,
            context_injections=build_injections(),
        )
        final = (result.get("final_result") or "").strip()
        err = result.get("error")
        if err:
            return "failed", final, str(err)
        return "success", final or "(无输出)", ""

    # ── 事件内容组装 ──────────────────────────────────────────────────

    def _build_prompt(self, event: EventBase) -> str:
        """把事件负载组装为思考提示词（记录 content 与发送给编排器共用）。"""
        if event.event_type == "rss.feed":
            parts = []
            title = str(event.get("entry_title") or "").strip()
            link = str(event.get("entry_link") or "").strip()
            summary = str(event.get("entry_summary") or "").strip()
            if title:
                parts.append(f"标题: {title}")
            if link:
                parts.append(f"链接: {link}")
            if summary:
                parts.append(f"摘要: {summary}")
            return "\n".join(parts)
        if event.event_type == "webhook.push":
            return str(event.get("content") or "").strip()
        return ""

    def _record_title(self, event: EventBase) -> str:
        """事件处理记录标题（日志列表展示用）。"""
        if event.event_type == "rss.feed":
            return str(event.get("entry_title") or "")[:100] or "(无标题)"
        if event.event_type == "webhook.push":
            origin = str(event.get("origin") or "").strip()
            return f"WebHook 推送（{origin}）" if origin else "WebHook 推送"
        return event.event_type


__all__ = ["ThinkingChannel", "format_result_message"]