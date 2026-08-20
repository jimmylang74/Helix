"""
Flask routes for iBot management.

Provides JSON-RPC methods and an SSE endpoint for real-time message push.
Registered as a Blueprint and attached to the admin Flask app.
"""

import json
from typing import Any, Dict

from flask import Blueprint, Response, request

from imBots import events
from imBots.manager import ChannelManager
from imBots.store import get_messages as store_get_messages
from imBots.wechat.channel import WeChatChannel
from modules.utils.logger import log_error, log_info

# Module-level reference set by configure()
_channel_manager: ChannelManager | None = None

imbot_bp = Blueprint("imbot", __name__, url_prefix="/api")


def configure(channel_manager: ChannelManager) -> None:
    """Inject the ChannelManager instance (called from Helix.py)."""
    global _channel_manager
    _channel_manager = channel_manager


def _mgr() -> ChannelManager:
    if _channel_manager is None:
        raise RuntimeError("ChannelManager not configured")
    return _channel_manager


def _get_wechat() -> WeChatChannel:
    """Get the WeChat channel, cast from abstract base."""
    ch = _mgr().get("wechat")
    if ch is None:
        raise ValueError("WeChat channel not registered")
    if not isinstance(ch, WeChatChannel):
        raise TypeError("Channel 'wechat' is not a WeChatChannel")
    return ch


# ── JSON-RPC Handlers ──────────────────────────────────────────────────────


def _imbot_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """List all registered channels and their status."""
    return {"channels": _mgr().list_channels()}


def _imbot_wechat_qrcode(params: Dict[str, Any]) -> Dict[str, Any]:
    """Request a WeChat QR code for login."""
    log_info(f"[iBot] QR code requested, params={list(params.keys())}")
    ch = _get_wechat()
    result = ch._auth.start_auth(**params)
    log_info(f"[iBot] QR code result keys={list(result.keys())}, has_error={'error' in result}")
    if "error" in result:
        raise ValueError(result["error"])
    return result


def _imbot_wechat_qrcode_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Poll WeChat QR code scan status."""
    log_info(f"[iBot] qrcode_status called, params={params}")
    ch = _get_wechat()
    result = ch._auth.check_auth_status(**params)
    log_info(f"[iBot] qrcode_status result: authenticated={result.get('authenticated')}, has_error={'error' in result}")
    return result


def _imbot_wechat_start(params: Dict[str, Any]) -> Dict[str, Any]:
    """Start WeChat long-polling."""
    ch = _get_wechat()
    if not ch._auth.is_authenticated:
        raise ValueError("Not authenticated — scan QR code first")
    timeout = params.get("poll_timeout")
    if timeout:
        ch.poll_timeout = int(timeout)
    ch.start()
    return {"success": True, "status": ch.get_status().to_dict()}


def _imbot_wechat_stop(params: Dict[str, Any]) -> Dict[str, Any]:
    """Stop WeChat long-polling."""
    ch = _get_wechat()
    ch.stop()
    return {"success": True, "status": ch.get_status().to_dict()}


def _imbot_wechat_messages(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get WeChat conversation history."""
    limit = int(params.get("limit", 50))  # type: ignore[arg-type]
    messages = store_get_messages("wechat", limit)
    return {"messages": messages}


def _imbot_wechat_send(params: Dict[str, Any]) -> Dict[str, Any]:
    """Send a message via WeChat."""
    ch = _get_wechat()
    content = params.get("content", "")
    if not content:
        raise ValueError("Missing 'content' in params")
    msg_type = params.get("msg_type", "text")
    result = ch.send(content, msg_type=msg_type)
    if "error" in result:
        raise ValueError(result["error"])
    return {"success": True, "result": result}


def _imbot_wechat_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get WeChat channel status."""
    try:
        ch = _get_wechat()
        return {"status": ch.get_status().to_dict()}
    except (ValueError, TypeError):
        return {"status": {"channel_type": "wechat", "is_running": False, "is_authenticated": False}}


def _imbot_wechat_logout(params: Dict[str, Any]) -> Dict[str, Any]:
    """Logout WeChat (clear session)."""
    ch = _get_wechat()
    if ch.is_running:
        ch.stop()
    ch._auth.logout()
    return {"success": True}


# ── Dispatch Table ─────────────────────────────────────────────────────────

IMBOT_METHODS = {
    "imbot.list":                _imbot_list,
    "imbot.wechat.qrcode":      _imbot_wechat_qrcode,
    "imbot.wechat.qrcode_status": _imbot_wechat_qrcode_status,
    "imbot.wechat.start":       _imbot_wechat_start,
    "imbot.wechat.stop":        _imbot_wechat_stop,
    "imbot.wechat.messages":    _imbot_wechat_messages,
    "imbot.wechat.send":        _imbot_wechat_send,
    "imbot.wechat.status":      _imbot_wechat_status,
    "imbot.wechat.logout":      _imbot_wechat_logout,
}


# ── SSE Endpoint ───────────────────────────────────────────────────────────


@imbot_bp.route("/imbot-stream")
def imbot_stream():
    """SSE endpoint for real-time iBot message push."""
    channel = request.args.get("channel", "wechat")
    cursor = int(request.args.get("cursor", 0))

    def gen():
        yield "data: " + json.dumps({"type": "snapshot"}) + "\n\n"
        yield from events.stream(channel, cursor=cursor, timeout=60)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
