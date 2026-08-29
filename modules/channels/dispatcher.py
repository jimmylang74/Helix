"""
OutputDispatcher — 通用输出通道注册表（跨通道消息推送基础设施）。

与 ChannelManager（通道生命周期管理）关注点分离：本模块只负责"输出路由"，
把一段文本/消息按逻辑通道 id 投递到已注册的 sink。sink 可以是任意 ChannelAdapter
实例或具备 ``send(content, **kwargs) -> dict`` 的可调用对象。

任何通道/子系统都可以把自己的输出机制注册为 sink，任何调用方都可以经
``send(channel_id, content)`` 跨通道推送结果。现有通道的映射均可表达：

- cron    : 默认输出仍是 db/cron.db 落库（消费者侧硬编码，不在此注册）；
            用户为任务额外配置的通道经任务字段 output_channels 经本模块路由
- wechat  : 通道本身即 iLinkBot 输出（register("ilinkbot", wechat_channel)）
- web     : SSE 广播输出（register("web_sse", web_channel) 按需注册一行即可）

约定：send() 绝不抛异常——未知/不可用通道返回 {"ok": False, "error": ...}，
调用方据此记日志；通道自身的返回 dict 透传至 detail 供上层使用。
"""

from threading import Lock
from typing import Any, Dict, List, Optional

from modules.utils.logger import log_error, log_info


class OutputDispatcher:
    """输出通道注册表：logical channel_id → sink（含展示 label）。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._sinks: Dict[str, Dict[str, Any]] = {}  # channel_id → {sink, label}

    # ── 注册表管理 ─────────────────────────────────────────────────────

    def register(self, channel_id: str, sink: Any, label: Optional[str] = None) -> None:
        """注册输出通道。sink 须具备 send(content, **kwargs) 且返回 dict。"""
        channel_id = str(channel_id or "").strip().lower()
        if not channel_id or sink is None:
            raise ValueError("register() requires a non-empty channel_id and a sink")
        display = label or getattr(sink, "display_name", None) or channel_id
        with self._lock:
            self._sinks[channel_id] = {"sink": sink, "label": str(display)}
        log_info(f"[OutputDispatcher] Registered output channel: {channel_id} ({display})")

    def unregister(self, channel_id: str) -> bool:
        """注销通道，返回是否曾注册。"""
        channel_id = str(channel_id or "").strip().lower()
        with self._lock:
            return self._sinks.pop(channel_id, None) is not None

    def is_registered(self, channel_id: str) -> bool:
        channel_id = str(channel_id or "").strip().lower()
        with self._lock:
            return channel_id in self._sinks

    def available(self) -> List[Dict[str, Any]]:
        """已注册通道列表（前端下拉/工具选项动态获取）：{id, label, available}。"""
        with self._lock:
            items = [
                {"id": cid, "label": info["label"], "available": True}
                for cid, info in self._sinks.items()
            ]
        return sorted(items, key=lambda x: x["id"])

    # ── 投递 ───────────────────────────────────────────────────────────

    def send(self, channel_id: str, content: str, **kwargs) -> Dict[str, Any]:
        """把 content 投递到指定输出通道。失败/未知通道返回错误 dict，绝不抛异常。"""
        channel_id = str(channel_id or "").strip().lower()
        with self._lock:
            info = self._sinks.get(channel_id)
        if info is None:
            return {"ok": False, "error": f"输出通道未注册: {channel_id}"}
        sink = info["sink"]
        try:
            result = sink.send(content, **kwargs)
        except Exception as e:  # sink 内部异常 → 记为投递失败，不外抛
            log_error(f"[OutputDispatcher] send to '{channel_id}' failed: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if isinstance(result, dict) and result.get("error"):
            return {"ok": False, "error": str(result["error"]), "detail": result}
        return {"ok": True, "detail": result}


# ── 进程级单例 ─────────────────────────────────────────────────────────────

_dispatcher: Optional[OutputDispatcher] = None
_dispatcher_lock = Lock()


def get_dispatcher() -> OutputDispatcher:
    """获取进程级唯一的输出通道注册表。"""
    global _dispatcher
    with _dispatcher_lock:
        if _dispatcher is None:
            _dispatcher = OutputDispatcher()
        return _dispatcher


__all__ = ["OutputDispatcher", "get_dispatcher"]