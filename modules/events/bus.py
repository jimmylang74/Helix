"""
EventBus — 外部世界输入事件总线（进程内发布/订阅）。

感知层各事件源（定时器线程 / IM 轮询线程 / 未来 Webhook/MCP/邮件）经
publish() 把 EventBase 子类**非阻塞**投递到本总线；总线为每个订阅者维护一条
独立 FIFO 队列与一个消费者线程，逐个回调 consumer(event)。

- publish() 绝不阻塞生产线程，也不感知"谁在处理本事件"
- 单进程单例（get_event_bus()），与 get_scheduler()/get_dispatcher() 同模式
- stop() 幂等：停止全部消费者线程（Helix 退出时经组合根调用）
- 观测：累计发布计数 + 最近事件环形缓冲（状态接口/调试用）
"""

import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from modules.events.base import EventBase
from modules.utils.logger import log_error, log_info

Consumer = Callable[[EventBase], None]

_RECENT_LIMIT = 200


class _Subscriber:
    """单个订阅者的队列 + 消费者线程（内部实现）。"""

    def __init__(self, consumer: Consumer) -> None:
        self.consumer: Consumer = consumer
        self._queue: "queue.Queue[Optional[EventBase]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def put(self, event: EventBase) -> None:
        self._queue.put(event)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="eventbus-consumer",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if event is None:
                continue
            try:
                self.consumer(event)
            except Exception as e:  # 消费方异常 → 记录，不终止消费线程
                log_error(f"[EventBus] Consumer error: {e}")


class EventBus:
    """进程内发布/订阅总线。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[_Subscriber] = []
        self._stopped = False

        # 观测：累计计数 + 最近事件环形缓冲
        self._total_published = 0
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=_RECENT_LIMIT)

    # ── 发布 / 订阅 ────────────────────────────────────────────────────

    def publish(self, event: EventBase) -> None:
        """非阻塞投递事件到所有订阅者队列（O(订阅者数)）。"""
        event.received_at = time.time()
        with self._lock:
            if self._stopped:
                log_error("[EventBus] publish after stop — dropped")
                return
            self._total_published += 1
            self._recent.append(event.to_dict())
            for sub in self._subscribers:
                sub.put(event)

    def subscribe(self, consumer: Consumer) -> None:
        """注册订阅者：为其创建独立队列 + 消费者线程。"""
        with self._lock:
            if self._stopped:
                raise RuntimeError("EventBus 已停止，无法再订阅")
            for sub in self._subscribers:
                if sub.consumer is consumer:
                    log_info("[EventBus] Consumer already subscribed — ignore")
                    return
            sub = _Subscriber(consumer)
            self._subscribers.append(sub)
        sub.start()
        log_info("[EventBus] Subscriber registered")

    def unsubscribe(self, consumer: Consumer) -> bool:
        """注销订阅者并停止其消费者线程，返回是否曾注册。"""
        with self._lock:
            for i, sub in enumerate(self._subscribers):
                if sub.consumer is consumer:
                    del self._subscribers[i]
                    sub.stop()
                    log_info("[EventBus] Subscriber unregistered")
                    return True
        return False

    def stop(self) -> None:
        """停止全部消费者线程（幂等）。"""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            subs = list(self._subscribers)
            self._subscribers = []
        for sub in subs:
            sub.stop()
        log_info(
            f"[EventBus] Stopped (total_published={self._total_published})"
        )

    # ── 观测 ───────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """事件总线状态摘要（状态接口/调试用）。"""
        with self._lock:
            return {
                "stopped": self._stopped,
                "subscribers": len(self._subscribers),
                "total_published": self._total_published,
                "recent": list(self._recent),
            }


# ── 进程级单例 ─────────────────────────────────────────────────────────────

_singleton: Optional[EventBus] = None
_singleton_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """获取进程级唯一的输入事件总线。"""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = EventBus()
        return _singleton


__all__ = ["EventBus", "Consumer", "get_event_bus"]