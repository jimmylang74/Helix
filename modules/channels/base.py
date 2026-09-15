"""
Abstract base classes for the IM channel adapter framework.

Provides a unified interface for different IM platforms (WeChat, Telegram,
Discord, etc.) so the Helix core can interact with all channels identically.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

from modules.events.base import EventBase
from modules.events.event_source import EventSource


# ── Data Models ────────────────────────────────────────────────────────────


@dataclass
class ChannelMessage:
    """Unified message model across all channels."""

    message_id: str
    channel: str                   # "wechat" / "telegram" / "discord"
    sender_id: str                 # sender identifier
    sender_name: str               # human-readable sender name
    content: str                   # text content
    msg_type: str                  # "text" / "image" / "file" / "audio"
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    context_token: Optional[str] = None  # reply context (e.g. WeChat)
    timestamp: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "channel": self.channel,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "msg_type": self.msg_type,
            "media_url": self.media_url,
            "media_type": self.media_type,
            "context_token": self.context_token,
            "timestamp": self.timestamp,
        }


@dataclass
class BotConfig:
    """Bot configuration — persisted to the store."""

    bot_id: str
    channel_type: str              # "wechat"
    bot_token: Optional[str] = None
    display_name: Optional[str] = None
    enabled: bool = True
    config_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "channel_type": self.channel_type,
            "bot_token": self.bot_token,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "config_data": self.config_data,
        }


@dataclass
class ChannelStatus:
    """Channel runtime status."""

    channel_type: str
    is_running: bool
    is_authenticated: bool
    display_name: str = ""
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel_type": self.channel_type,
            "is_running": self.is_running,
            "is_authenticated": self.is_authenticated,
            "display_name": self.display_name,
            "error": self.error,
            **self.extra,
        }


# ── Abstract Base Classes ──────────────────────────────────────────────────


class ChannelAuthenticator(ABC):
    """Channel-specific authentication.

    WeChat uses QR-code scan, Telegram/Discord use token entry, etc.
    """

    @abstractmethod
    def start_auth(self, **kwargs) -> Dict[str, Any]:
        """Initiate the authentication flow.

        For WeChat: call get_bot_qrcode and return {qrcode_id, qrcode_img_content}.
        For token-based channels: validate the token and return status.
        """
        ...

    @abstractmethod
    def check_auth_status(self, **kwargs) -> Dict[str, Any]:
        """Poll / check whether authentication completed.

        Returns at minimum {"authenticated": bool}.
        """
        ...

    @abstractmethod
    def logout(self) -> bool:
        """Invalidate the current session / token."""
        ...


class ChannelClient(ABC):
    """Low-level protocol client for a specific IM backend."""

    @abstractmethod
    def send_message(
        self,
        recipient: str,
        content: str,
        msg_type: str = "text",
        **kwargs,
    ) -> Dict[str, Any]:
        """Send a message to the IM server.

        Returns the server response dict.
        """
        ...

    @abstractmethod
    def poll_updates(
        self, timeout: int = 50
    ) -> List[Dict[str, Any]]:
        """Long-poll for new messages.

        Returns a list of raw message dicts from the server.
        """
        ...

    @abstractmethod
    def get_config(self) -> Dict[str, Any]:
        """Fetch server-side channel configuration."""
        ...

    @abstractmethod
    def send_typing(self, **kwargs) -> Dict[str, Any]:
        """Indicate 'typing' status to the peer."""
        ...


class ChannelAdapter(ABC):
    """High-level channel adapter combining auth + client + lifecycle.

    Each concrete adapter (WeChat, Telegram, ...) exposes a simple
    start / stop / send interface. 外部世界事件的感知已抽离为 EventSource
    对象（轮询 getupdates / 定时器 tick 等），通道按需持有：单源通道可直接
    持有并委托 start/stop，多源通道用 attach_source 挂载（_start_sources /
    _stop_sources 随通道启停驱动）。
    """

    # 通道私有运行时（组合根调用 build_channel_runtime 后挂载，
    # 见 modules/channels/runtime.py）
    runtime: Any = None

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """Unique channel identifier, e.g. 'wechat'."""
        ...

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether the polling loop is active."""
        ...

    @abstractmethod
    def start(self) -> None:
        """Start the channel (launch polling thread, etc.)."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the channel gracefully."""
        ...

    @abstractmethod
    def send(self, content: str, msg_type: str = "text", **kwargs) -> Dict[str, Any]:
        """Send a message through this channel."""
        ...

    @abstractmethod
    def get_messages(self, limit: int = 50) -> List[ChannelMessage]:
        """Return recent messages (newest first)."""
        ...

    @abstractmethod
    def get_status(self) -> ChannelStatus:
        """Return current runtime status."""
        ...

    @abstractmethod
    def restore_session(self) -> bool:
        """Try to restore a previously saved session from the store.

        Returns True if a valid session was restored and polling can resume.
        """
        ...

    # ── 事件源挂载（多源通道 / 独立源的统一生命周期管理）──────────────────
    #
    # 事件源是"感知端"（轮询 getupdates / 定时器 tick / 未来 Webhook 注册后
    # 待命），通道是"消费端"（handle_event）。单源通道可直接构造并持有事件源
    # （如 WeChatChannel 持 WechatEventSource）并随 start/stop 委托；需要挂载
    # 多个源的通道用本组方法登记，由 _start_sources/_stop_sources 统一驱动。

    def __init__(self) -> None:
        self._event_sources: Dict[str, EventSource] = {}

    def attach_source(self, source: EventSource) -> bool:
        """挂载一个事件源（以 source_name 为键）。重复挂载返回 False。"""
        if source.source_name in self._event_sources:
            return False
        self._event_sources[source.source_name] = source
        return True

    def detach_source(self, source_name: str) -> bool:
        """卸载事件源（不停止其线程）。返回是否曾挂载。"""
        if source_name not in self._event_sources:
            return False
        del self._event_sources[source_name]
        return True

    @property
    def event_sources(self) -> List[EventSource]:
        """已挂载事件源列表（按挂载顺序）。"""
        return list(self._event_sources.values())

    def _start_sources(self) -> None:
        """启动全部挂载事件源（逐个 start，各幂等）。"""
        for source in self._event_sources.values():
            source.start()

    def _stop_sources(self) -> None:
        """停止全部挂载事件源（逐个 stop，各幂等）。"""
        for source in self._event_sources.values():
            source.stop()

    # ── 通道相关工具的通道侧实现（注册见 runtime.py）─────────────────

    @abstractmethod
    def ask_user(self, request_id: str, question: str) -> str:
        """向本通道用户提问并阻塞等待回答（本通道 ask_user 工具的落点）。

        request_id 为发起本次 agent 请求的请求 ID；返回交给 LLM 的回答文本。
        """
        ...

    @abstractmethod
    def get_context(self) -> str:
        """返回本通道会话历史上下文（本通道 get_context 工具的落点）。"""
        ...

    @abstractmethod
    def clear_context(self) -> str:
        """清除本通道会话历史并开启新会话（clear_context 工具的落点）。"""
        ...

    # ── 外部事件处理器入口（EventBroker 路由落点）─────────────────────

    def handle_event(self, event: EventBase) -> None:
        """处理经 EventBroker 路由而来的外部世界事件（如微信消息事件）。

        默认不处理任何事件；需要接收外部事件输入的通道覆写此方法。
        生产者（轮询线程/定时器线程）只负责感知并 publish 事件，本方法
        是"谁来处理"的解耦落点 —— 路由决策在 EventBroker 一处。
        """
        return None
