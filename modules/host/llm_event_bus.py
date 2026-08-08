"""
LlmEventBusImpl — LlmEventBus 抽象基类的 Host 侧实现。

包装 modules.llm.llm_events 的线程安全事件总线（请求上下文 + LLM 事件流），
由组合根实例化后注入 AgentOrchestrator（event_bus 端口）。
"""

from typing import Any, Dict, Optional

from HelixCore.interface import LlmEventBus
from modules.llm import llm_events


class LlmEventBusImpl(LlmEventBus):
    """LlmEventBus 实现 — 转接到 modules.llm.llm_events 事件总线。"""

    def set_request_context(self, request_id: str) -> None:
        llm_events.set_request_context(request_id)

    def clear_request_context(self) -> None:
        llm_events.clear_request_context()

    def get_request_context(self) -> Optional[str]:
        return llm_events.get_request_context()

    def emit(self, request_id: str, event: Dict[str, Any]) -> None:
        llm_events.emit(request_id, event)

    def cleanup(self, request_id: str) -> None:
        llm_events.cleanup(request_id)
