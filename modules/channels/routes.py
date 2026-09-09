"""
Flask routes for iBot management.

Provides JSON-RPC methods and an SSE endpoint for real-time message push.
Registered as a Blueprint and attached to the admin Flask app.
"""

import json
from typing import Any, Dict

from flask import Blueprint, Response, request

from modules.channels import events
from modules.channels.manager import ChannelManager
from modules.channels.store import get_messages as store_get_messages
from imChannels.wechat.channel import WeChatChannel
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


def _imbot_wechat_get_config(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get the WeChat channel config (download dir + poll settings)."""
    from modules.config.config_manager import ConfigManager
    from modules.utils.paths import get_download_dir

    cm = ConfigManager()
    poll_timeout = params.get("poll_timeout")
    if poll_timeout:
        ch = _get_wechat()
        ch.poll_timeout = int(poll_timeout)
    return {
        "config": {
            "download_dir": cm.get_wechat_download_dir(),
            "download_dir_abs": get_download_dir(),
            "poll_timeout": _get_wechat().poll_timeout,
        }
    }


def _imbot_wechat_set_config(params: Dict[str, Any]) -> Dict[str, Any]:
    """Persist WeChat channel config (download dir, poll timeout)."""
    from modules.config.config_manager import ConfigManager
    from modules.utils.paths import get_download_dir

    cm = ConfigManager()
    download_dir = params.get("download_dir")
    if download_dir is not None:
        cm.set("channels.wechat.download_dir", str(download_dir).strip())

    poll_timeout = params.get("poll_timeout")
    if poll_timeout:
        ch = _get_wechat()
        ch.poll_timeout = int(poll_timeout)
        cm.set("channels.wechat.poll_timeout", int(poll_timeout))

    return {
        "success": True,
        "config": {
            "download_dir": cm.get_wechat_download_dir(),
            "download_dir_abs": get_download_dir(),
            "poll_timeout": _get_wechat().poll_timeout,
        },
    }


def _imbot_wechat_send_file(params: Dict[str, Any]) -> Dict[str, Any]:
    """Send a file (document) from a local path via WeChat."""
    ch = _get_wechat()
    file_path = params.get("file_path", "")
    if not file_path:
        raise ValueError("Missing 'file_path' in params")
    result = ch.send_file(
        file_path,
        to_user_id=params.get("to_user_id"),
        context_token=params.get("context_token"),
        display_name=params.get("display_name") or "",
    )
    if "error" in result:
        raise ValueError(result["error"])
    return {"success": True, "result": result}


def _imbot_wechat_send_voice(params: Dict[str, Any]) -> Dict[str, Any]:
    """Send a voice message from a local path via WeChat."""
    ch = _get_wechat()
    file_path = params.get("file_path", "")
    if not file_path:
        raise ValueError("Missing 'file_path' in params")
    result = ch.send_voice(
        file_path,
        to_user_id=params.get("to_user_id"),
        context_token=params.get("context_token"),
        display_name=params.get("display_name") or "",
    )
    if "error" in result:
        raise ValueError(result["error"])
    return {"success": True, "result": result}


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
    "imbot.wechat.get_config":  _imbot_wechat_get_config,
    "imbot.wechat.set_config":  _imbot_wechat_set_config,
    "imbot.wechat.send_file":   _imbot_wechat_send_file,
    "imbot.wechat.send_voice":  _imbot_wechat_send_voice,
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
