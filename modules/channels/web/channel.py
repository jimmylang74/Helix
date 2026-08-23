"""
WebChannel — 管理控制台"快速测试"页对应的通道。

RPC ``agent/router`` 的请求都归属本通道：其编排器为该通道私有实例，
ask_user 经 llm_events 推送 SSE 事件给前端并阻塞等待回答；
get_context / clear_context 使用全局会话集（history_store）。
"""

from typing import Any, Dict, List

from modules.channels.base import ChannelAdapter, ChannelMessage, ChannelStatus
from modules.llm.llm_events import emit as _emit_llm_event
from modules.channels.web import history_store
from modules.utils.logger import log_debug, log_tool_call


class WebChannel(ChannelAdapter):
    """Web 通道 — 无轮询线程，请求经 RPC 直接进入私有编排器。"""

    CHANNEL_TYPE = "web"

    def __init__(self):
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    @property
    def channel_type(self) -> str:
        return self.CHANNEL_TYPE

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        log_debug("[Web] Channel started")

    def stop(self) -> None:
        self._running = False
        log_debug("[Web] Channel stopped")

    def restore_session(self) -> bool:
        """Web 通道无持久会话，装配即用。"""
        return True

    # ── Messaging ──────────────────────────────────────────────────────

    def send(self, content: str, msg_type: str = "text", **kwargs) -> Dict[str, Any]:
        """Web 通道无独立推送出口 — 结果经 SSE 流按 request_id 下发。"""
        return {"channel": self.CHANNEL_TYPE, "delivered": False}

    def get_messages(self, limit: int = 50) -> List[ChannelMessage]:
        return []

    def get_status(self) -> ChannelStatus:
        return ChannelStatus(
            channel_type=self.CHANNEL_TYPE,
            is_running=self._running,
            is_authenticated=True,
            display_name="Web 快速测试",
            extra={"transport": "rpc+sse"},
        )

    # ── 通道工具落点 ───────────────────────────────────────────────────

    def ask_user(self, request_id: str, question: str) -> str:
        if self.runtime is None or self.runtime.broker is None:
            return "错误: Web 通道运行时尚未装配，无法提问"
        broker = self.runtime.broker
        if broker.is_waiting(request_id):
            return "错误: 已有一个等待用户回答的问题，请等待其回答完成，不要重复提问"
        log_tool_call(f"[web] ask_user(question='{question[:200]}')")
        _emit_llm_event(request_id, {"type": "ask_user", "question": question})
        return broker.ask(request_id, question)

    def get_context(self) -> str:
        context_list = history_store.get_session_context()
        if not context_list:
            return "当前会话集中没有历史记录"
        parts = []
        for i, item in enumerate(context_list, 1):
            parts.append(
                f"--- 请求 {i} ---\n"
                f"用户请求: {item['user_request']}\n"
                f"最终结果: {item['final_answer']}"
            )
        return "\n\n".join(parts)

    def clear_context(self) -> str:
        old_id = history_store.archive_current_session()
        new_id = history_store.get_current_session_id()
        return f"会话上下文已清除。旧会话集 {old_id} 已归档，新会话集 {new_id} 已开始。"
