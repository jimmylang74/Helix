"""
EventSource — 外部世界事件源抽象基类（生产者侧统一契约）。

所有把外部世界"感知"封装为 EventBase 并投递到 EventBus 的源头共享同一形态：
都运行至少一个工作线程（轮询 getupdates / tick 扫描 / Webhook 注册路由后待命），
在线程内感知 + 生成事件 + publish。本基类固化该共性并统一生命周期与观测：

- start()/stop() 幂等：守护线程包装子类实现的 _loop()；循环异常终止时记录 _last_error
- publish(event)   ：非阻塞投递到构造时绑定的 EventBus，并累计本源的产出计数
- get_status()     ：统一状态摘要（运行状态 / 产出计数 / 最近事件时刻 / 错误 / 线程存活）

具体事件源（CronEventSource / WechatEventSource / 未来 Webhook/Email/RSS）应：
- 覆写 source_name（路由/观测用，如 "cron"/"wechat"）与 thread_name（工作线程名）
- 实现 _loop()；按需覆写 _prepare_start_locked()/_after_stop_locked() 钩子
  做启动前前置（如 Cron 重算触发表）与停止后清理
- 使用 self.publish(event) 产出事件 —— 路由决策集中在 EventBroker，生产者不感知消费者

事件驱动型源（如 Webhook）：其 _loop() 注册完外部触发器后
self._stop_event.wait() 待命（工作线程仍存在，契约一致），事件由外部请求线程
触发 on_request() → publish()。
"""

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from modules.events.base import EventBase
from modules.events.bus import EventBus, get_event_bus
from modules.utils.logger import log_error, log_info


def _iso(ts: Optional[float]) -> Optional[str]:
    """时间戳 → 本地时间 ISO 文本（观测接口友好）；None 原样返回。"""
    if ts is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class EventSource(ABC):
    """外部世界事件源基类：统一生命周期（工作线程）+ 产出观测。

    类属性：
    - source_name  : 源标识（观测/注册表键用，如 "cron"/"wechat"），子类必须覆写
    - thread_name  : 工作线程名，子类覆写
    - join_timeout : stop() 等待线程退出的秒数（默认 10.0）
    """

    source_name: str = ""
    thread_name: str = ""
    join_timeout: float = 10.0

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self._event_bus: EventBus = event_bus or get_event_bus()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_error: Optional[str] = None

        # 产出观测：累计发布计数 + 最近一次发布时刻
        self._events_produced = 0
        self._last_event_at: Optional[float] = None

    # ── 生命周期 ───────────────────────────────────────────────────────

    def start(self) -> bool:
        """启动工作线程；已在运行则不做任何事。返回是否真正启动。"""
        with self._lock:
            if self.is_running:
                log_info(f"[{self.source_name}] Already running — ignore")
                return False
            self._stop_event.clear()
            self._last_error = None
            self._prepare_start_locked()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name=self.thread_name,
            )
            self._thread.start()
        log_info(f"[{self.source_name}] Started")
        return True

    def stop(self) -> bool:
        """停止工作线程；已停止则不做任何事。返回是否真正停止。"""
        with self._lock:
            if not self.is_running:
                log_info(f"[{self.source_name}] Already stopped — ignore")
                return False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
            self._after_stop_locked()
        if thread and thread.is_alive():
            thread.join(timeout=self.join_timeout)
        log_info(f"[{self.source_name}] Stopped")
        return True

    @property
    def is_running(self) -> bool:
        """线程态即事实：线程存活且停止信号未置位。"""
        return bool(
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    # ── 钩子（子类按需覆写）──────────────────────────────────────────────

    def _prepare_start_locked(self) -> None:
        """启动线程前、持锁状态下的前置处理（默认无操作）。"""

    def _after_stop_locked(self) -> None:
        """停止置位后、线程退出前的状态清理（默认无操作）。"""

    # ── 工作循环 ───────────────────────────────────────────────────────

    def _run(self) -> None:
        """线程包装：_loop 未处理异常 → 记录为最后错误并置位停止信号，线程随之终止。"""
        try:
            self._loop()
        except Exception as e:  # 循环异常终止 → 记入观测
            with self._lock:
                self._last_error = str(e)
                self._stop_event.set()
            log_error(f"[{self.source_name}] Loop terminated with error: {e}")

    @abstractmethod
    def _loop(self) -> None:
        """工作线程主循环：感知外部世界并 self.publish(EventBase)。

        轮询型源在此 while not self._stop_event.wait(...) 循环；
        事件驱动型源注册完外部触发器后 self._stop_event.wait() 待命
        （事件由外部线程触发 on_request() → publish()）。
        """

    # ── 产出 ───────────────────────────────────────────────────────────

    def publish(self, event: EventBase) -> None:
        """非阻塞投递事件到绑定的 EventBus，并累计本源产出计数。"""
        self._event_bus.publish(event)
        with self._lock:
            self._events_produced += 1
            self._last_event_at = time.time()

    # ── 观测 ───────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """当前状态摘要：运行状态 / 产出计数 / 最近事件时刻 / 错误 / 线程存活。"""
        with self._lock:
            return {
                "source": self.source_name,
                "status": "running" if self.is_running else "stopped",
                "events_produced": self._events_produced,
                "last_event_at": _iso(self._last_event_at),
                "last_error": self._last_error,
                "thread_alive": bool(self._thread and self._thread.is_alive()),
            }

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(source={self.source_name}, "
            f"running={self.is_running})"
        )


__all__ = ["EventSource"]