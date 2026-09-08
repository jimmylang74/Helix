"""
EventBroker — 事件分发器（EventBus 的唯一消费者）。

订阅 EventBus 后，按 event.event_type 从路由表中查找处理器（Channel），把事件
交给 processor.handle_event(event)。路由决策集中在此一处：

- 新增消息源：只需新增事件子类 + register() 一行，无需新增 Channel
- 切换处理方（如未来由 Thinking Channel 统一思考）：只需改 register 的 processor，
  所有生产者零改动
- 未注册类型：记 warning 丢弃，不抛异常（绝不拖垮订阅线程或生产线程）
- 处理器异常：捕获记日志，隔离于其他事件的处理/后续事件
"""

import threading
from typing import Any, Dict, Optional

from modules.events.base import EventBase
from modules.utils.logger import log_error, log_info, log_warning


class EventBroker:
    """按 event_type 路由事件到已注册处理器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: Dict[str, Any] = {}
        self._handled = 0        # 成功分发次数
        self._failed = 0         # 处理器抛异常次数
        self._dropped = 0        # 未注册类型被丢弃次数

    # ── 路由表管理 ─────────────────────────────────────────────────────

    def register(self, event_type: str, processor: Any) -> None:
        """注册路由：event_type → 具备 handle_event(event) 的处理器。"""
        event_type = str(event_type or "").strip()
        if not event_type:
            raise ValueError("register() requires a non-empty event_type")
        if not hasattr(processor, "handle_event"):
            raise TypeError(
                f"processor for '{event_type}' must implement handle_event(event)"
            )
        with self._lock:
            self._routes[event_type] = processor
        log_info(
            f"[EventBroker] Route: {event_type} → {type(processor).__name__}"
        )

    def unregister(self, event_type: str) -> bool:
        """注销路由，返回是否曾注册。"""
        event_type = str(event_type or "").strip()
        with self._lock:
            return self._routes.pop(event_type, None) is not None

    def route(self, event_type: str) -> Optional[Any]:
        """查找 event_type 对应的处理器；未注册返回 None。"""
        with self._lock:
            return self._routes.get(event_type)

    # ── 消费：EventBus 订阅线程回调 ────────────────────────────────────

    def __call__(self, event: EventBase) -> None:
        """EventBus 消费者入口（subscribe 时传 broker 实例本身）。"""
        self.dispatch(event)

    def dispatch(self, event: EventBase) -> None:
        """路由并调用处理器；任何失败都以日志/计数形式消化，不抛出。"""
        processor = self.route(event.event_type)
        if processor is None:
            with self._lock:
                self._dropped += 1
            log_warning(
                f"[EventBroker] No processor for event_type="
                f"{event.event_type} (event_id={event.event_id}) — dropped"
            )
            return
        try:
            processor.handle_event(event)
        except Exception as e:  # 处理器异常 → 隔离，不影响订阅线程/后续事件
            with self._lock:
                self._failed += 1
            log_error(
                f"[EventBroker] Handler for '{event.event_type}' failed: {e}"
            )
        else:
            with self._lock:
                self._handled += 1

    # ── 观测 ───────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """路由表与分发统计（状态接口/调试用）。"""
        with self._lock:
            return {
                "routes": {k: type(v).__name__ for k, v in self._routes.items()},
                "handled": self._handled,
                "failed": self._failed,
                "dropped": self._dropped,
            }


# ── 进程级单例 ─────────────────────────────────────────────────────────────

_singleton: Optional[EventBroker] = None
_singleton_lock = threading.Lock()


def get_event_broker() -> EventBroker:
    """获取进程级唯一的事件分发器。"""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = EventBroker()
        return _singleton


__all__ = ["EventBroker", "get_event_broker"]