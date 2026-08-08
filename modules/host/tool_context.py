"""
Host-side execution context for tools — ask_user / request_id / tool_registry.

Tools run inside a request; the context carries everything they need without
importing host modules directly.  ``current()`` builds a context from the
thread-local request context set by the orchestrator (same source the old
AskUserTool used via ``get_request_context``); explicit construction is used
by the RPC answer path and orchestrator cancellation.
"""

from typing import Optional

from HelixCore.tools.base import tool_registry
from modules.core.user_question import user_question_broker
from modules.llm.llm_events import get_request_context, emit as _emit_llm_event
from modules.utils.logger import log_tool_call


class ToolContext:
    """Per-request context handed to tools during execution."""

    def __init__(self, request_id: str, broker=None, registry=None, emit=None):
        self.request_id = request_id
        self._broker = broker or user_question_broker
        self._registry = registry or tool_registry
        self._emit = emit or _emit_llm_event

    @classmethod
    def current(cls) -> Optional["ToolContext"]:
        """Build a context from the active thread-local request context."""
        request_id = get_request_context()
        if not request_id:
            return None
        return cls(request_id)

    @property
    def tool_registry(self):
        return self._registry

    def ask_user(self, question: str) -> str:
        """Register a question and block until the user answers.

        Mirrors the original AskUserTool flow: duplicate-question guard,
        ask_user event to the frontend, then blocking broker.ask().
        """
        request_id = self.request_id
        if not request_id:
            return "错误: ask_user 需要活跃的请求上下文（request_id），当前无法提问"
        if self._broker.is_waiting(request_id):
            return "错误: 已有一个等待用户回答的问题，请等待其回答完成，不要重复提问"
        log_tool_call(f"ask_user(question='{question[:200]}')")
        self._emit(request_id, {"type": "ask_user", "question": question})
        return self._broker.ask(request_id, question)

    def answer(self, answer: str) -> bool:
        """Deliver the user's answer to a pending question."""
        return self._broker.answer(self.request_id, answer)

    def cancel(self) -> bool:
        """Cancel a pending question (request cancelled / finished)."""
        return self._broker.cancel(self.request_id)
