"""
WeChat iLinkBot channel adapter — 消费端（感知已抽离至 WechatEventSource）。

本模块不再拥有独立轮询线程：getupdates 长轮询、媒体感知、落库、SSE 广播与
WechatEvent 发布整体迁入 modules/channels/wechat/event_source.py（EventSource 子类）。
本通道保留消费端职责：持有事件源（self._source）并委托 start/stop，send/send_file/
send_voice 发送，handle_event 按发送方状态路由（待回答提问→broker.answer /
任务进行中→忙碌提示 / 新请求→worker 跑私有编排器并回发结果）。
"""

import os
import threading
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
)
from modules.events import EventBase
from modules.channels.wechat.authenticator import WeChatAuthenticator
from modules.channels.wechat.ilink_client import (
    ILinkBotsClient,
    MEDIA_TYPE_FILE,
    MEDIA_TYPE_VOICE,
    UPLOAD_MEDIA_TYPE_FILE,
    UPLOAD_MEDIA_TYPE_VOICE,
)
from modules.channels.wechat.crypto import (
    aes_encrypt,
    encode_aes_key_wire,
    generate_aes_key,
)
from modules.utils.logger import log_error, log_info, log_tool_call
# 感知层辅助函数自事件源模块迁出；此处重导出以保持既有引用兼容
# （channel.send 内部使用 + tests/test_media.py 直接导入 channel）
from modules.channels.wechat.event_source import (
    WechatEventSource,
    _extract_text,
    _media_type_str,
    _media_msg_type,
    _safe_media_name,
    _media_extension,
    _estimate_voice_seconds,
    _now,
)


class WeChatChannel(ChannelAdapter):
    """WeChat channel — long-poll for messages and expose send()."""

    CHANNEL_TYPE = "wechat"

    def __init__(
        self,
        client: ILinkBotsClient,
        authenticator: WeChatAuthenticator,
        poll_timeout: int = 35,
    ):
        super().__init__()
        self._client = client
        self._auth = authenticator
        # 感知端：轮询/媒体/落库/SSE/发布整体迁入，生命周期随本通道启停委托
        self._source = WechatEventSource(
            client=client,
            authenticator=authenticator,
            poll_timeout=poll_timeout,
        )

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
        return self._source.is_running

    @property
    def poll_timeout(self) -> int:
        return self._source.poll_timeout

    @poll_timeout.setter
    def poll_timeout(self, value: int) -> None:
        self._source.poll_timeout = value

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the polling event source (auth gate handled inside the source)."""
        self._source.start()

    def stop(self) -> None:
        """Stop the polling event source thread."""
        self._source.stop()

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
        to_user_id = kwargs.pop("to_user_id", None) or self._source.last_from_user_id or get_to_user_id("wechat")
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
        to_user_id = to_user_id or self._source.last_from_user_id or get_to_user_id("wechat")
        context_token = context_token or get_context_token("wechat")

        if not to_user_id:
            return {"error": "No to_user_id available"}
        if not context_token:
            return {"error": "No context_token available"}
        if not os.path.isfile(file_path):
            return {"error": f"File not found: {file_path}"}

        with open(file_path, "rb") as fp:
            raw = fp.read()

        media, err = self._upload_and_get_media(
            file_path, UPLOAD_MEDIA_TYPE_FILE, raw, to_user_id
        )
        if err:
            return {"error": err}

        name = display_name or os.path.basename(file_path)
        item = {
            "type": MEDIA_TYPE_FILE,
            "file_item": {
                "media": media,
                "file_name": name,
                "md5": self._client._md5(raw),
                "len": str(len(raw)),
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
        to_user_id = to_user_id or self._source.last_from_user_id or get_to_user_id("wechat")
        context_token = context_token or get_context_token("wechat")

        if not to_user_id:
            return {"error": "No to_user_id available"}
        if not context_token:
            return {"error": "No context_token available"}
        if not os.path.isfile(file_path):
            return {"error": f"File not found: {file_path}"}

        with open(file_path, "rb") as fp:
            raw = fp.read()

        media, err = self._upload_and_get_media(
            file_path, UPLOAD_MEDIA_TYPE_VOICE, raw, to_user_id
        )
        if err:
            return {"error": err}

        item = {
            "type": MEDIA_TYPE_VOICE,
            "voice_item": {
                "media": media,
                "playtime": _estimate_voice_seconds(len(raw)) * 1000,
            },
        }
        return self._send_media_message(item, to_user_id, context_token, msg_type="voice")

    def _upload_and_get_media(self, file_path: str, media_type: int,
                              raw: bytes, to_user_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """Encrypt + upload ``raw`` to the media CDN.

        Returns ``({encrypt_query_param, aes_key, encrypt_type}, error)`` —
        the CDN media reference to embed in a ``sendmessage`` item.
        """
        aes_key = generate_aes_key()
        encrypted = aes_encrypt(raw, aes_key)
        filekey = uuid.uuid4().hex

        upload_resp = self._client.getuploadurl(
            filekey=filekey,
            media_type=media_type,
            to_user_id=to_user_id,
            raw_size=len(raw),
            raw_file_md5=self._client._md5(raw),
            encrypted_size=len(encrypted),
            aes_key=aes_key,
        )
        upload_param = upload_resp.get("upload_param") or ""
        if not upload_param:
            return None, (
                f"getuploadurl returned no upload_param "
                f"(ret={upload_resp.get('ret')}, errmsg={upload_resp.get('errmsg')}, "
                f"keys={list(upload_resp.keys())})"
            )

        encrypt_param = self._client.upload_media(
            upload_param, aes_key, raw, filekey
        )
        if not encrypt_param:
            return None, "CDN upload returned no x-encrypted-param"

        return {
            "encrypt_query_param": encrypt_param,
            "aes_key": encode_aes_key_wire(aes_key),
            "encrypt_type": 1,
        }, ""

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

    def get_messages(self, limit: int = 50) -> List[ChannelMessage]:
        raw = store_get_messages("wechat", limit)
        return [_raw_to_message(m) for m in raw]

    def get_status(self) -> ChannelStatus:
        source_status = self._source.get_status()
        source_error = self._source.last_error
        return ChannelStatus(
            channel_type=self.CHANNEL_TYPE,
            is_running=self._source.is_running,
            is_authenticated=self._auth.is_authenticated,
            display_name="微信",
            error=source_error,
            extra={
                "poll_timeout": self._source.poll_timeout,
                "thread_alive": source_status["thread_alive"],
                "has_token": bool(self._client.bot_token),
                "token_expired": "errcode -14" in (source_error or ""),
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
                    resp = self.send_file(file_path, to_user_id=sender_id)
                except Exception as e:
                    log_error(
                        f"[WeChat] Failed to send generated file {file_path}: {e}"
                    )
                    continue
                if isinstance(resp, dict) and resp.get("error"):
                    log_error(
                        f"[WeChat] Failed to send generated file {file_path}: {resp['error']}"
                    )
                    self.send(
                        f"文件已生成，但发送失败: {resp['error']}",
                        to_user_id=sender_id,
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
