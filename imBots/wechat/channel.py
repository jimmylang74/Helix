"""
WeChat iLinkBot channel adapter.

Runs a background polling thread that calls getupdates() in a loop,
broadcasts incoming messages via SSE, and provides a send() method.
"""

import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from imBots.base import ChannelAdapter, ChannelMessage, ChannelStatus
from imBots import events
from imBots.store import (
    get_context_token,
    get_messages as store_get_messages,
    save_message,
    update_session_status,
)
from imBots.wechat.authenticator import WeChatAuthenticator
from imBots.wechat.ilink_client import ILinkBotsClient
from modules.utils.logger import log_error, log_info


class WeChatChannel(ChannelAdapter):
    """WeChat channel — long-poll for messages and expose send()."""

    CHANNEL_TYPE = "wechat"

    def __init__(
        self,
        client: ILinkBotsClient,
        authenticator: WeChatAuthenticator,
        poll_timeout: int = 50,
    ):
        self._client = client
        self._auth = authenticator
        self._poll_timeout = poll_timeout
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_error: Optional[str] = None

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def channel_type(self) -> str:
        return self.CHANNEL_TYPE

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def poll_timeout(self) -> int:
        return self._poll_timeout

    @poll_timeout.setter
    def poll_timeout(self, value: int) -> None:
        self._poll_timeout = max(5, value)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the polling thread."""
        if self._running:
            log_info("[WeChat] Already running")
            return
        if not self._auth.is_authenticated:
            log_error("[WeChat] Cannot start: not authenticated")
            return

        self._stop_event.clear()
        self._running = True
        self._last_error = None
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="wechat-poll",
        )
        self._thread.start()
        update_session_status("wechat", "connected")
        log_info("[WeChat] Polling started")

    def stop(self) -> None:
        """Stop the polling thread."""
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        update_session_status("wechat", "disconnected")
        log_info("[WeChat] Polling stopped")

    def restore_session(self) -> bool:
        """Restore session from store and auto-start if valid."""
        if self._auth.restore_from_store():
            self.start()
            return True
        return False

    # ── Send ───────────────────────────────────────────────────────────

    def send(self, content: str, msg_type: str = "text", **kwargs) -> Dict[str, Any]:
        """Send a message using the latest context_token."""
        context_token = kwargs.pop("context_token", None) or get_context_token("wechat")
        if not context_token:
            return {"error": "No context_token available — wait for an incoming message"}

        result = self._client.sendmessage(
            context_token=context_token,
            content=content,
            msg_type=msg_type,
            **kwargs,
        )

        # Persist outgoing message
        msg_id = f"out_{uuid.uuid4().hex[:12]}"
        save_message(
            channel="wechat",
            direction="outgoing",
            message_id=msg_id,
            content=content,
            msg_type=msg_type,
            context_token=context_token,
        )

        # Broadcast to SSE subscribers
        events.broadcast("wechat", {
            "type": "message",
            "direction": "outgoing",
            "message_id": msg_id,
            "content": content,
            "msg_type": msg_type,
            "timestamp": _now(),
        })

        return result

    # ── Messages ───────────────────────────────────────────────────────

    def get_messages(self, limit: int = 50) -> List[ChannelMessage]:
        raw = store_get_messages("wechat", limit)
        return [_raw_to_message(m) for m in raw]

    def get_status(self) -> ChannelStatus:
        return ChannelStatus(
            channel_type=self.CHANNEL_TYPE,
            is_running=self._running,
            is_authenticated=self._auth.is_authenticated,
            display_name="微信 iLinkBot",
            error=self._last_error,
            extra={"poll_timeout": self._poll_timeout},
        )

    # ── Polling loop ───────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                data = self._client.getupdates(timeout=self._poll_timeout)
                updates = data.get("updates", [])
                if updates:
                    consecutive_errors = 0
                    for update in updates:
                        self._handle_update(update)
            except Exception as e:
                consecutive_errors += 1
                self._last_error = str(e)
                log_error(f"[WeChat] Poll error ({consecutive_errors}): {e}")
                # Back off on repeated errors
                backoff = min(30, 2 ** consecutive_errors)
                self._stop_event.wait(timeout=backoff)
                continue

            # Brief pause between polls to avoid hammering
            self._stop_event.wait(timeout=1.0)

        log_info("[WeChat] Poll loop exited")

    def _handle_update(self, update: Dict[str, Any]) -> None:
        """Process a single incoming message from getupdates."""
        msg_id = update.get("msg_id", str(uuid.uuid4().hex[:12]))
        sender_id = update.get("from_user", "")
        sender_name = update.get("from_user_name", sender_id)
        content = update.get("content", "")
        msg_type = update.get("msg_type", "text")
        context_token = update.get("context_token", "")
        media_url = update.get("media_url", None)
        media_type = update.get("media_type", None)
        timestamp = update.get("timestamp", _now())

        # Persist
        save_message(
            channel="wechat",
            direction="incoming",
            message_id=msg_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            msg_type=msg_type,
            media_url=media_url,
            media_type=media_type,
            context_token=context_token,
            raw_data=update,
            timestamp=timestamp,
        )

        # Broadcast to SSE
        events.broadcast("wechat", {
            "type": "message",
            "direction": "incoming",
            "message_id": msg_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "msg_type": msg_type,
            "media_url": media_url,
            "media_type": media_type,
            "context_token": context_token,
            "timestamp": timestamp,
        })

        log_info(
            f"[WeChat] Message received: {sender_name}({sender_id}): "
            f"{content[:80]}"
        )


# ── Helpers ────────────────────────────────────────────────────────────────


def _raw_to_message(raw: Dict[str, Any]) -> ChannelMessage:
    return ChannelMessage(
        message_id=raw.get("message_id", ""),
        channel=raw.get("channel", "wechat"),
        sender_id=raw.get("sender_id", ""),
        sender_name=raw.get("sender_name", ""),
        content=raw.get("content", ""),
        msg_type=raw.get("msg_type", "text"),
        media_url=raw.get("media_url"),
        media_type=raw.get("media_type"),
        context_token=raw.get("context_token"),
        timestamp=raw.get("timestamp", ""),
        raw=raw.get("raw_data", {}),
    )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
