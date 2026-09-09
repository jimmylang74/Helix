"""
WeChat iLinkBot channel adapter.

Runs a background polling thread that calls getupdates() in a loop,
broadcasts incoming messages via SSE, and provides a send() method.

感知解耦：轮询线程只负责"感知 + 记录 + 广播"，随后把每条消息封装为
WechatEvent 投递到 EventBus，由 EventBroker 按事件类型路由回本通道的
handle_event() 处理（待回答提问→broker.answer / 任务进行中→忙碌提示 /
新请求→worker 跑私有编排器并回发结果）。生产者不再直接决定处理流程。
"""

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from modules.channels.base import ChannelAdapter, ChannelMessage, ChannelStatus
from modules.channels import events
from modules.channels.store import (
    archive_agent_session,
    get_active_agent_context,
    get_context_token,
    get_messages as store_get_messages,
    get_to_user_id,
    save_agent_context,
    save_message,
    update_session_status,
)
from modules.events import EventBase, WechatEvent, get_event_bus
from imChannels.wechat.authenticator import WeChatAuthenticator
from imChannels.wechat.ilink_client import (
    ILinkBotsClient,
    MEDIA_TYPE_FILE,
    MEDIA_TYPE_IMAGE,
    MEDIA_TYPE_VIDEO,
    MEDIA_TYPE_VOICE,
    parse_media_item,
)
from imChannels.wechat.crypto import aes_encrypt, generate_aes_key
from modules.utils.logger import log_error, log_info, log_tool_call
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


class WeChatChannel(ChannelAdapter):
    """WeChat channel — long-poll for messages and expose send()."""

    CHANNEL_TYPE = "wechat"

    def __init__(
        self,
        client: ILinkBotsClient,
        authenticator: WeChatAuthenticator,
        poll_timeout: int = 35,
    ):
        self._client = client
        self._auth = authenticator
        self._poll_timeout = poll_timeout
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_error: Optional[str] = None
        self._last_from_user_id: Optional[str] = None

        # 本通道私有 agent 会话状态（配合 runtime 使用；上下文持久化见 store.agent_sessions）
        self._request_sender: Dict[str, str] = {}    # request_id → sender_id
        self._active_by_sender: Dict[str, str] = {}  # sender_id → 运行中的 request_id
        self._pending_ask: Dict[str, str] = {}       # sender_id → 等待回答的 request_id

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
        has_token = bool(self._client.bot_token)
        update_session_status("wechat", "connected")
        log_info(
            f"[WeChat] Polling started (has_token={has_token}, "
            f"poll_timeout={self._poll_timeout}, thread_alive={self._thread.is_alive()})"
        )

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
        """Send a message via iLink.

        Resolves ``to_user_id`` from kwargs, the latest incoming message,
        or the stored context_token.
        """
        to_user_id = kwargs.pop("to_user_id", None) or self._last_from_user_id or get_to_user_id("wechat")
        context_token = kwargs.pop("context_token", None) or get_context_token("wechat")

        if not to_user_id:
            return {"error": "No to_user_id available — wait for an incoming message"}
        if not context_token:
            return {"error": "No context_token available — wait for an incoming message"}

        result = self._client.sendmessage(
            to_user_id=to_user_id,
            content=content,
            context_token=context_token,
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

    # ── Media send / download ──────────────────────────────────────────

    def send_file(self, file_path: str, to_user_id: Optional[str] = None,
                  context_token: Optional[str] = None,
                  display_name: str = "") -> Dict[str, Any]:
        """Upload and send a file (document) to ``to_user_id``.

        ``file_path`` is typically an LLM-produced output under ``output/``.
        The file bytes are read, encrypted, uploaded to the media CDN, then a
        ``sendmessage`` with a type-4 ``file_item`` referencing the media_id
        is sent. Returns the iLink API response or an error dict.
        """
        to_user_id = to_user_id or self._last_from_user_id or get_to_user_id("wechat")
        context_token = context_token or get_context_token("wechat")

        if not to_user_id:
            return {"error": "No to_user_id available"}
        if not context_token:
            return {"error": "No context_token available"}
        if not os.path.isfile(file_path):
            return {"error": f"File not found: {file_path}"}

        with open(file_path, "rb") as fp:
            raw = fp.read()

        media_id, err = self._upload_and_get_media_id(
            file_path, MEDIA_TYPE_FILE, raw, to_user_id
        )
        if err:
            return {"error": err}

        name = display_name or os.path.basename(file_path)
        item = {
            "type": MEDIA_TYPE_FILE,
            "file_item": {
                "file_name": name,
                "file_size": len(raw),
                "media_id": media_id,
                "file_url": "",
            },
        }
        return self._send_media_message(item, to_user_id, context_token, msg_type="file")

    def send_voice(self, file_path: str, to_user_id: Optional[str] = None,
                   context_token: Optional[str] = None,
                   display_name: str = "") -> Dict[str, Any]:
        """Upload and send a voice message to ``to_user_id``.

        ``file_path`` is typically an audio file produced under ``output/``.
        Sends a type-3 ``voice_item`` carrying the uploaded media_id.
        """
        to_user_id = to_user_id or self._last_from_user_id or get_to_user_id("wechat")
        context_token = context_token or get_context_token("wechat")

        if not to_user_id:
            return {"error": "No to_user_id available"}
        if not context_token:
            return {"error": "No context_token available"}
        if not os.path.isfile(file_path):
            return {"error": f"File not found: {file_path}"}

        with open(file_path, "rb") as fp:
            raw = fp.read()

        media_id, err = self._upload_and_get_media_id(
            file_path, MEDIA_TYPE_VOICE, raw, to_user_id
        )
        if err:
            return {"error": err}

        name = display_name or os.path.basename(file_path)
        item = {
            "type": MEDIA_TYPE_VOICE,
            "voice_item": {
                "voice_code": 4,
                "play_length": _estimate_voice_seconds(len(raw)),
                "media_id": media_id,
                "voice_url": "",
            },
        }
        return self._send_media_message(item, to_user_id, context_token, msg_type="voice")

    def _upload_and_get_media_id(self, file_path: str, media_type: int,
                                 raw: bytes, to_user_id: str) -> Tuple[Optional[str], str]:
        """Encrypt + upload a file to CDN, returning ``(media_id, error)``."""
        aes_key = generate_aes_key()
        encrypted = aes_encrypt(raw, aes_key)
        filekey = os.path.basename(file_path)

        upload_resp = self._client.getuploadurl(
            filekey=filekey,
            media_type=media_type,
            to_user_id=to_user_id,
            file_size=len(raw),
            aes_key=aes_key,
            file_md5=self._client._md5(encrypted),
            raw_file_md5=self._client._md5(raw),
        )
        upload_param = upload_resp.get("upload_param") or upload_resp.get("data") or {}
        media_id = (
            upload_resp.get("media_id")
            or upload_param.get("media_id")
            or upload_param.get("file_id")
            or ""
        )
        if not media_id and not upload_param:
            return None, f"getuploadurl returned no upload_param: {list(upload_resp.keys())}"

        if upload_param:
            self._client.upload_media(upload_param, aes_key, raw)
        return media_id or upload_param.get("media_id", ""), ""

    def _send_media_message(self, item: Dict[str, Any], to_user_id: str,
                            context_token: str, msg_type: str) -> Dict[str, Any]:
        """Send a media item via sendmessage and persist/broadcast it."""
        result = self._client.send_media(
            to_user_id=to_user_id,
            context_token=context_token,
            item_list=[item],
        )
        msg_id = f"out_{uuid.uuid4().hex[:12]}"
        save_message(
            channel="wechat",
            direction="outgoing",
            message_id=msg_id,
            content=item.get("file_item", {}).get("file_name")
            or item.get("voice_item", {}).get("media_id", ""),
            msg_type=msg_type,
            media_type=item.get("type"),
            context_token=context_token,
        )
        events.broadcast("wechat", {
            "type": "message",
            "direction": "outgoing",
            "message_id": msg_id,
            "content": _extract_text({"item_list": [item]}),
            "msg_type": msg_type,
            "context_token": context_token,
            "timestamp": _now(),
        })
        return result

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

    def get_messages(self, limit: int = 50) -> List[ChannelMessage]:
        raw = store_get_messages("wechat", limit)
        return [_raw_to_message(m) for m in raw]

    def get_status(self) -> ChannelStatus:
        thread_alive = self._thread.is_alive() if self._thread else False
        if self._running and not thread_alive:
            self._last_error = self._last_error or "polling thread died unexpectedly"
        return ChannelStatus(
            channel_type=self.CHANNEL_TYPE,
            is_running=self._running,
            is_authenticated=self._auth.is_authenticated,
            display_name="微信 iLinkBot",
            error=self._last_error,
            extra={
                "poll_timeout": self._poll_timeout,
                "thread_alive": thread_alive,
                "has_token": bool(self._client.bot_token),
                "token_expired": "errcode -14" in (self._last_error or ""),
                "get_updates_buf": self._client.get_updates_buf[:32] if self._client.get_updates_buf else "",
            },
        )

    # ── 通道工具落点（ask_user / 会话上下文）───────────────────────────

    def ask_user(self, request_id: str, question: str) -> str:
        if self.runtime is None or self.runtime.broker is None:
            return "错误: 微信通道运行时尚未装配，无法提问"
        broker = self.runtime.broker
        if broker.is_waiting(request_id):
            return "错误: 已有一个等待用户回答的问题，请等待其回答完成，不要重复提问"
        log_tool_call(f"[wechat] ask_user(question='{question[:200]}')")
        sender_id = self._request_sender.get(request_id, "")
        if not sender_id:
            return "错误: 无法定位提问目标用户，请基于已有信息继续任务"
        self.send(f"[提问] {question}", to_user_id=sender_id)
        self._pending_ask[sender_id] = request_id
        try:
            return broker.ask(request_id, question)
        finally:
            self._pending_ask.pop(sender_id, None)

    def get_context(self) -> str:
        """返回本通道进行中会话的全部内容（已归档会话不参与拼装）。"""
        entries = get_active_agent_context(self.CHANNEL_TYPE)
        if not entries:
            return "本通道暂无进行中的会话记录"
        parts = []
        for i, item in enumerate(entries, 1):
            parts.append(
                f"--- 请求 {i} ---\n"
                f"用户请求: {item['user_request']}\n"
                f"最终结果: {item['final_answer']}"
            )
        return "\n\n".join(parts)

    def clear_context(self) -> str:
        """归档当前会话（全部记录保存入库）并开始新会话。"""
        archived_id, count = archive_agent_session(self.CHANNEL_TYPE)
        if count == 0:
            return "本通道没有进行中的会话，无需清除"
        return f"微信通道已开始新会话：旧会话 {archived_id}（{count} 条记录）已保存到数据库。"

    # ── Polling loop ───────────────────────────────────────────────────

    def _poll_loop(self) -> None:
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

        self._running = False
        log_info("[WeChat] Poll loop exited")

    def _handle_update(self, update: Dict[str, Any]) -> None:
        """Process a single incoming message from getupdates (iLink format)."""
        msg_id = update.get("msg_id", str(uuid.uuid4().hex[:12]))
        sender_id = update.get("from_user_id", "")
        sender_name = update.get("from_user_name", sender_id)
        content = _extract_text(update)
        context_token = update.get("context_token", "")
        message_type = update.get("message_type", 1)
        timestamp_ms = update.get("create_time_ms")

        # Detect + download actionable media (files & voice) to the download dir
        media_url, media_type = self._download_incoming_media(update)

        if media_url:
            content = f"{content}\n文件已下载: {media_url}"

        # Track last from_user_id for send()
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

        # Persist
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

        # Broadcast to SSE
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

        # 感知解耦：封装为 WechatEvent 投递到外部事件总线，
        # 由 EventBroker 路由到本通道 handle_event()（或未来的 Thinking Channel）
        get_event_bus().publish(
            WechatEvent(
                sender_id=sender_id,
                sender_name=sender_name,
                content=content,
                context_token=context_token,
                raw=update,
            )
        )

    # ── 外部事件处理器（EventBroker 路由落点）─────────────────────────

    def handle_event(self, event: EventBase) -> None:
        """EventBroker 分发入口：微信消息事件 → 按发送方状态路由处理。"""
        if self.runtime is None:
            log_error("[WeChat] Runtime not assembled — dropping agent processing")
            return
        sender_id = event.get("sender_id", "") or ""
        sender_name = event.get("sender_name", "") or ""
        content = event.get("content", "") or ""
        self._dispatch_incoming(sender_id, sender_name, content)

    # ── Agent dispatch（私有 runtime 路由）─────────────────────────────

    def _dispatch_incoming(self, sender_id: str, sender_name: str, content: str) -> None:
        """按发送方状态路由：待回答提问 / 任务进行中 / 新起 worker。"""
        if self.runtime is None:
            log_error("[WeChat] Runtime not assembled — dropping agent processing")
            return

        pending_req = self._pending_ask.get(sender_id)
        if pending_req:
            answered = self.runtime.broker.answer(pending_req, content)
            if not answered:
                log_error(f"[WeChat] Answer for '{pending_req}' arrived too late — dropped")
            return

        if sender_id in self._active_by_sender:
            self.send("[提示] 当前有任务正在处理中，请稍候再发送新消息", to_user_id=sender_id)
            return

        threading.Thread(
            target=self._run_agent,
            args=(sender_id, sender_name, content),
            daemon=True,
            name=f"wechat-agent-{sender_id[:8]}",
        ).start()

    def _run_agent(self, sender_id: str, sender_name: str, content: str) -> None:
        """Worker 线程：跑本通道私有编排器并把最终结果回发微信。"""
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        self._request_sender[request_id] = sender_id
        self._active_by_sender[sender_id] = request_id
        try:
            result = self.runtime.orchestrator.process_request(content, request_id)
            final = result.get("final_result") or ""
            error = result.get("error")
            reply = final if final else (f"处理失败: {error}" if error else "")
            if reply:
                self.send(reply, to_user_id=sender_id)
                save_agent_context(self.CHANNEL_TYPE, content, reply)
            for file_path in (result.get("generated_files") or []):
                try:
                    self.send_file(file_path, to_user_id=sender_id)
                except Exception as e:
                    log_error(
                        f"[WeChat] Failed to send generated file {file_path}: {e}"
                    )
        except Exception as e:
            log_error(f"[WeChat] Agent request {request_id} failed: {e}")
            try:
                self.send(f"处理出错: {e}", to_user_id=sender_id)
            except Exception as send_err:
                log_error(f"[WeChat] Failed to deliver error message: {send_err}")
        finally:
            self._active_by_sender.pop(sender_id, None)
            self._request_sender.pop(request_id, None)
            # 请求结束即唤醒可能仍阻塞的 ask_user（与 RPC 路径收尾一致）
            self.runtime.broker.cancel(request_id)


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
