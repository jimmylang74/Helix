"""
EventSourceRegistry — 进程内事件源注册表（统一管理所有源的生命周期）。

具体事件源（CronEventSource / WechatEventSource / 未来 Webhook/Email/RSS）在
组合根（Helix.py）装配时登记到本注册表，即可获得一体化的启停管理：

- 归属某个 Channel 的源：由 Channel 生命周期驱动（attach_source 后随通道启停）
- 独立于任何 Channel 的源（如 Web Hook）：只需登记进注册表即可被管理

- register/unregister 以 source_name 为键，幂等（同名单源复注册忽略）
- start_all()/stop_all() 幂等，逐个驱动各源启停（Helix 退出时经组合根调用）
- snapshot() 汇总各源 get_status() 供状态接口/调试用
"""

import threading
from typing import Any, Dict, List, Optional

from modules.events.event_source import EventSource
from modules.utils.logger import log_error, log_info


class EventSourceRegistry:
    """以 source_name 为键的事件源注册表（进程级单例，经 get_source_registry() 获取）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: Dict[str, EventSource] = {}

    # ── 登记 / 注销 ────────────────────────────────────────────────────

    def register(self, source: EventSource) -> bool:
        """登记事件源；同名已登记则忽略。返回是否新登记。"""
        if not source.source_name:
            raise ValueError("EventSource 必须设置非空 source_name")
        with self._lock:
            if source.source_name in self._sources:
                log_info(
                    f"[EventSourceRegistry] Source '{source.source_name}' "
                    f"already registered — ignore"
                )
                return False
            self._sources[source.source_name] = source
        log_info(f"[EventSourceRegistry] Registered '{source.source_name}'")
        return True

    def unregister(self, source_name: str) -> bool:
        """注销事件源（不停止其线程）。返回是否曾登记。"""
        with self._lock:
            if source_name not in self._sources:
                return False
            del self._sources[source_name]
        log_info(f"[EventSourceRegistry] Unregistered '{source_name}'")
        return True

    # ── 查询 ───────────────────────────────────────────────────────────

    def get(self, source_name: str) -> Optional[EventSource]:
        with self._lock:
            return self._sources.get(source_name)

    def get_all(self) -> List[EventSource]:
        with self._lock:
            return list(self._sources.values())

    # ── 生命周期（批量）────────────────────────────────────────────────

    def start_all(self) -> None:
        """启动全部已登记源（各 start 幂等；失败仅记录不中断）。"""
        for source in self.get_all():
            try:
                source.start()
            except Exception as e:
                log_error(
                    f"[EventSourceRegistry] Failed to start "
                    f"'{source.source_name}': {e}"
                )

    def stop_all(self) -> None:
        """停止全部已登记源（各 stop 幂等；Helix 退出时经组合根调用）。"""
        for source in self.get_all():
            try:
                source.stop()
            except Exception as e:
                log_error(
                    f"[EventSourceRegistry] Failed to stop "
                    f"'{source.source_name}': {e}"
                )

    # ── 观测 ───────────────────────────────────────────────────────────

    def snapshot(self) -> List[Dict[str, Any]]:
        """各源状态摘要汇总。"""
        return [source.get_status() for source in self.get_all()]


# ── 进程级单例 ─────────────────────────────────────────────────────────────

_registry: Optional[EventSourceRegistry] = None
_registry_lock = threading.Lock()


def get_source_registry() -> EventSourceRegistry:
    """获取进程级唯一的事件源注册表。"""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = EventSourceRegistry()
        return _registry


__all__ = ["EventSourceRegistry", "get_source_registry"]