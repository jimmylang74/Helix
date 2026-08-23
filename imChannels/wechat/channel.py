"""
WeChat iLinkBot channel adapter.

Runs a background polling thread that calls getupdates() in a loop,
broadcasts incoming messages via SSE, and provides a send() method.

Each incoming text message is routed to this channel's private runtime:
pending ask_user answers go to the broker, messages from a sender with an
active request get a busy hint, otherwise a worker thread runs the private
orchestrator and sends the final result back to the sender.
"""

import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

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
from imChannels.wechat.authenticator import WeChatAuthenticator
from imChannels.wechat.ilink_client import ILinkBotsClient
from modules.utils.logger import log_error, log_info, log_tool_call


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

    # ── Messages ───────────────────────────────────────────────────────

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
            msg_type="text",
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
            "msg_type": "text",
            "context_token": context_token,
            "timestamp": timestamp,
        })

        log_info(
            f"[WeChat] Message received: {sender_name}({sender_id}): "
            f"{content[:80]}"
        )

        # Route to the channel's private agent runtime
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
