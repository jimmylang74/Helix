"""
事件输出端口 — HelixCore 向前端推送状态事件的唯一契约。

EventSink 是运行时注入的 Protocol：Host 侧（如 SSE 事件总线适配器）实现它，
HelixCore 只依赖该抽象，不关心事件如何被序列化、缓冲或推送到浏览器。

方法面与旧 status_events 保持一致（emit / cleanup），签名逐参数透传。
"""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class EventSink(Protocol):
    """事件输出接口 — 由 Host 侧实现并注入 AgentOrchestrator。

    ``emit`` 推送一次状态快照（可附带 DAG 节点图与单节点结果），
    ``cleanup`` 释放某个请求的全部事件缓冲与消费者队列。
    """

    def emit(
        self,
        request_id: str,
        state: Dict[str, Any],
        graph_nodes: Optional[List[Dict[str, Any]]] = None,
        node_result: Optional[Dict[str, Any]] = None,
        completed: bool = False,
    ) -> None:
        """Emit a status snapshot for ``request_id`` (transport-agnostic)."""
        ...

    def cleanup(self, request_id: str) -> None:
        """Release all buffered events and consumer queues for ``request_id``."""
        ...
