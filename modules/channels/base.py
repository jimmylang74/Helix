"""
Abstract base classes for the IM channel adapter framework.

Provides a unified interface for different IM platforms (WeChat, Telegram,
Discord, etc.) so the Helix core can interact with all channels identically.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional


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

    Each concrete adapter (WeChat, Telegram, ...) owns one long-polling
    background thread and exposes a simple start / stop / send interface.
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
