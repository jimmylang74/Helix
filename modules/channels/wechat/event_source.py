"""
WechatEventSource — 微信 iLinkBot 事件源（EventSource 子类，**纯事件生产者**）。

从 WeChatChannel 抽离的感知层：原 channel._poll_loop/_handle_update/
_download_incoming_media 与媒体辅助函数整体迁入，channel 侧保留消费层
（handle_event → agent 分发、send/ask_user 三件套、会话状态）。

本源的职责（原 channel 感知循环的 1-5 件事，作为 EventSource 具体子类直接实现）：
1. 媒体感知增强：下载可执行媒体（文件/语音）到 download 目录并在内容后附加路径
2. save_message 入库（incoming 消息持久化到 SQLite messages 表）
3. events.broadcast —— SSE 推送给前端（通道信号非 EventBus 事件）
4. 记录 last_from_user_id（供 channel.send() 解析 to_user_id）
5. self.publish(WechatEvent) —— 投递外部事件输入总线，由 EventBroker 路由
   回 channel.handle_event()（或未来的 Thinking Channel）

生命周期与观测继承基类：start/stop 幂等 + publish 计数 + get_status；
持有 authenticator 与 client —— token 失效时在轮询线程内直接清理认证状态
（置 _authenticated=False、清 bot_token、session 标记 token_expired）并退出循环。
"""

import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from modules.channels import events
from modules.channels.store import save_message, update_session_status
from modules.events import EventSource, WechatEvent
from modules.channels.wechat.authenticator import WeChatAuthenticator
from modules.channels.wechat.ilink_client import (
    ILinkBotsClient,
    MEDIA_TYPE_FILE,
    MEDIA_TYPE_IMAGE,
    MEDIA_TYPE_VIDEO,
    MEDIA_TYPE_VOICE,
    parse_media_item,
)
from modules.utils.logger import log_error, log_info
from modules.utils.paths import get_download_dir


def _extract_text(message: Dict[str, Any]) -> str:
    """Extract readable text from an iLink item_list message."""
    for item in message.get("item_list") or []:
        item_type = item.get("type")
        if item_type == 1 and item.get("text_item", {}).get("text"):
            return item["text_item"]["text"]
        if item_type == 3 and item.get("voice_item", {}).get("text"):
            return f"[语音] {item['voice_item']['text']}"
        if item_type == 2:
            return "[图片]"
        if item_type == 4:
            file_name = item.get("file_item", {}).get("file_name", "")
            return f"[文件] {file_name}".strip()
        if item_type == 5:
            return "[视频]"
    return "[空消息]"


def _media_type_str(media_type: int) -> str:
    """Map an iLink media type code to a readable channel msg_type."""
    if media_type == MEDIA_TYPE_VOICE:
        return "voice"
    if media_type == MEDIA_TYPE_FILE:
        return "file"
    if media_type == MEDIA_TYPE_IMAGE:
        return "image"
    if media_type == MEDIA_TYPE_VIDEO:
        return "video"
    return "text"


def _media_msg_type(media_type: Optional[str]) -> Optional[str]:
    """Return the ``msg_type`` label derived from an incoming download."""
    return media_type or None


def _safe_media_name(key: str, media_type: int) -> str:
    """Build a local filename for an incoming media item.

    Uses the original filename when safe; otherwise falls back to a
    timestamped name with the item's media-type extension.
    """
    base = os.path.basename(str(key or "").strip())
    if base and base not in (".", ".."):
        return "".join(c for c in base if c not in '\\/:*?"<>|')
    ext = _media_extension(media_type)
    return f"media_{int(time.time())}{ext}"


def _media_extension(media_type: int) -> str:
    if media_type == MEDIA_TYPE_VOICE:
        return ".amr"
    if media_type == MEDIA_TYPE_FILE:
        return ".bin"
    return ".dat"


def _estimate_voice_seconds(byte_count: int) -> int:
    """Rough voice duration in seconds (~2 KB/s at common WeChat AMR rates)."""
    return max(1, round(byte_count / 2048))


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class WechatEventSource(EventSource):
    """微信 iLinkBot 事件源：getupdates 长轮询线程，感知即 1-5 件事直接落库/广播/发布。"""

    source_name = "wechat"
    thread_name = "wechat-poll"

    def __init__(
        self,
        client: ILinkBotsClient,
        authenticator: WeChatAuthenticator,
        poll_timeout: int = 35,
        event_bus=None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self._client = client
        self._auth = authenticator
        self._poll_timeout = poll_timeout
        self._last_from_user_id: Optional[str] = None

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def poll_timeout(self) -> int:
        return self._poll_timeout

    @poll_timeout.setter
    def poll_timeout(self, value: int) -> None:
        self._poll_timeout = max(5, value)

    @property
    def last_from_user_id(self) -> Optional[str]:
        """最近一条 incoming 消息的 from_user_id（供 channel.send() 解析 to_user_id）。"""
        return self._last_from_user_id

    @property
    def last_error(self) -> Optional[str]:
        """最近一次轮询/处理错误（供 channel.get_status() 观测；线程异常终止也记录于此）。"""
        return self._last_error

    # ── 生命周期（认证门槛 + session 状态打点）────────────────────────

    def start(self) -> bool:
        if not self._auth.is_authenticated:
            log_error("[WeChat] Cannot start: not authenticated")
            return False
        # 状态先于线程启动置位：轮询线程内后续写（如 token_expired）必然晚于此写，
        # 最终会话状态正确反映循环终止后的真实状态，而非被本处覆盖
        update_session_status("wechat", "connected")
        started = super().start()
        if started:
            has_token = bool(self._client.bot_token)
            log_info(
                f"[WeChat] Polling started (has_token={has_token}, "
                f"poll_timeout={self._poll_timeout}, thread_alive={self.is_running})"
            )
        return started

    def stop(self) -> bool:
        stopped = super().stop()
        if stopped:
            update_session_status("wechat", "disconnected")
            log_info("[WeChat] Polling stopped")
        return stopped

    # ── 感知循环 ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        consecutive_errors = 0
        cycle_count = 0
        while not self._stop_event.is_set():
            cycle_count += 1
            try:
                data = self._client.getupdates(timeout=self._poll_timeout)

                # Check for token error (errcode or ret == -14)
                errcode = data.get("errcode") or data.get("ret")
                if errcode is not None and errcode != 0:
                    if self._client.is_token_error(data):
                        log_error("[WeChat] Bot token expired/invalid — stopping poll loop")
                        self._last_error = "bot_token invalid (errcode -14), please re-scan QR code"
                        self._auth._authenticated = False
                        self._client.set_bot_token("")
                        update_session_status("wechat", "token_expired")
                        break
                    else:
                        log_error(f"[WeChat] getupdates returned errcode={errcode}: {data.get('errmsg', '')}")

                msgs = data.get("msgs", [])
                if msgs:
                    consecutive_errors = 0
                    log_info(f"[WeChat] Poll #{cycle_count}: got {len(msgs)} msg(s)")
                    for msg in msgs:
                        self._handle_update(msg)
                else:
                    if cycle_count <= 3 or cycle_count % 10 == 0:
                        log_info(f"[WeChat] Poll #{cycle_count}: 0 msgs")
            except Exception as e:
                consecutive_errors += 1
                self._last_error = str(e)
                log_error(f"[WeChat] Poll error ({consecutive_errors}): {e}")
                backoff = min(30, 2 ** consecutive_errors)
                self._stop_event.wait(timeout=backoff)
                continue

            self._stop_event.wait(timeout=1.0)

        log_info("[WeChat] Poll loop exited")

    # ── 单条消息处理（1-5 件事直接落地）────────────────────────────────

    def _handle_update(self, update: Dict[str, Any]) -> None:
        """Process a single incoming message from getupdates (iLink format).

        事件源职责到此为止：感知增强 + 落库 + SSE 广播 + 记录
        last_from_user_id + publish(WechatEvent) —— 不感知谁处理、不决定路由。
        """
        msg_id = update.get("msg_id", str(uuid.uuid4().hex[:12]))
        sender_id = update.get("from_user_id", "")
        sender_name = update.get("from_user_name", sender_id)
        content = _extract_text(update)
        context_token = update.get("context_token", "")
        message_type = update.get("message_type", 1)
        timestamp_ms = update.get("create_time_ms")

        # 1. 感知增强：检测 + 下载可执行媒体（文件/语音）到 download 目录
        media_url, media_type = self._download_incoming_media(update)

        if media_url:
            content = f"{content}\n文件已下载: {media_url}"

        # 4. 记录 last_from_user_id（供 channel.send() 解析 to_user_id）
        if sender_id:
            self._last_from_user_id = sender_id

        # Convert timestamp
        timestamp = _now()
        if timestamp_ms:
            try:
                timestamp = time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.gmtime(timestamp_ms / 1000),
                )
            except (ValueError, OSError):
                pass

        # 2. 落库
        save_message(
            channel="wechat",
            direction="incoming",
            message_id=msg_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            msg_type=_media_msg_type(media_type) or "text",
            media_url=media_url,
            media_type=media_type,
            context_token=context_token,
            raw_data=update,
            timestamp=timestamp,
        )

        # 3. SSE 广播给前端
        events.broadcast("wechat", {
            "type": "message",
            "direction": "incoming",
            "message_id": msg_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "msg_type": _media_msg_type(media_type) or "text",
            "media_url": media_url,
            "media_type": media_type,
            "context_token": context_token,
            "timestamp": timestamp,
        })

        log_info(
            f"[WeChat] Message received: {sender_name}({sender_id}): "
            f"{content[:80]}"
        )

        # 5. 感知解耦：封装为 WechatEvent 投递到外部事件总线，
        #    由 EventBroker 路由到 channel.handle_event()（或未来的 Thinking Channel）
        self.publish(
            WechatEvent(
                sender_id=sender_id,
                sender_name=sender_name,
                content=content,
                context_token=context_token,
                raw=update,
            )
        )

    # ── 媒体感知 ───────────────────────────────────────────────────────

    def _download_incoming_media(self, update: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Download actionable incoming media (file/voice) to the download dir.

        Returns ``(media_url, media_type)`` where ``media_url`` is the local
        absolute path on success (``None`` otherwise).
        """
        for item in update.get("item_list") or []:
            media = parse_media_item(item)
            if not media or media["type"] not in (MEDIA_TYPE_FILE, MEDIA_TYPE_VOICE):
                continue
            safe_name = _safe_media_name(media["key"], media["type"])
            dest = os.path.join(get_download_dir(), safe_name)
            try:
                self._client.download_media(media, dest)
                log_info(f"[WeChat] Saved incoming {media['type']} media to {dest}")
                return dest, _media_type_str(media["type"])
            except Exception as e:
                log_error(f"[WeChat] Failed to download incoming media {media.get('key')}: {e}")
                return None, None
        return None, None


__all__ = [
    "WechatEventSource",
    # 辅助函数自 channel.py 迁入；channel.py 顶部重导出以保持既有引用兼容
    "_extract_text",
    "_media_type_str",
    "_media_msg_type",
    "_safe_media_name",
    "_media_extension",
    "_estimate_voice_seconds",
    "_now",
]