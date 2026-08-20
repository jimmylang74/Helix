"""
WeChat iLinkBot authentication — QR-code scan login flow.

Implements ChannelAuthenticator for the WeChat channel.
"""

import threading
from typing import Any, Dict, Optional

import qrcode
from io import BytesIO
import base64

from imBots.base import BotConfig, ChannelAuthenticator
from imBots.store import save_session, get_session
from imBots.wechat.ilink_client import ILinkBotsClient
from modules.utils.logger import log_error, log_info


class WeChatAuthenticator(ChannelAuthenticator):
    """QR-code based authentication for WeChat iLinkBot."""

    def __init__(self, client: ILinkBotsClient, proxy: Optional[str] = None):
        self._client = client
        self._proxy = proxy
        self._qrcode_id: Optional[str] = None
        self._qrcode_img_content: Optional[str] = None
        self._authenticated = False
        self._poll_lock = threading.Lock()
        self._last_poll_result: Optional[Dict[str, Any]] = None

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    def start_auth(self, **kwargs) -> Dict[str, Any]:
        """Initiate QR code login.

        Returns::

            {
                "qrcode_id": "...",
                "qrcode_img_content": "...",
                "qrcode_img_base64": "data:image/png;base64,..."
            }
        """
        try:
            data = self._client.get_bot_qrcode()
        except Exception as e:
            log_error(f"[WeChat Auth] Failed to get QR code: {e}")
            return {"error": str(e)}

        self._qrcode_id = data.get("qrcode", "")
        self._qrcode_img_content = data.get("qrcode_img_content", "")
        self._authenticated = False
        log_info(f"[WeChat Auth] start_auth: _qrcode_id={self._qrcode_id!r}, _authenticated={self._authenticated}")

        # Generate QR code image as base64 data URL
        qr_img_base64 = ""
        if self._qrcode_img_content:
            try:
                img = qrcode.make(self._qrcode_img_content)
                buf = BytesIO()
                img.save(buf, "PNG")
                qr_img_base64 = (
                    "data:image/png;base64,"
                    + base64.b64encode(buf.getvalue()).decode("utf-8")
                )
            except Exception as e:
                log_error(f"[WeChat Auth] Failed to generate QR image: {e}")

        return {
            "qrcode_id": self._qrcode_id,
            "qrcode_img_content": self._qrcode_img_content,
            "qrcode_img_base64": qr_img_base64,
        }

    def check_auth_status(self, **kwargs) -> Dict[str, Any]:
        """Poll the QR code scan status.

        Uses a lock so only one concurrent request hits the iLink API.
        Other requests return the cached result immediately.

        Returns::

            {"authenticated": bool, "bot_token": "...", "status": "..."}
        """
        log_info(f"[WeChat Auth] check_auth_status called, _qrcode_id={self._qrcode_id!r}, _authenticated={self._authenticated}")

        if self._authenticated:
            log_info("[WeChat Auth] Already authenticated — returning early")
            return {"authenticated": True}

        if not self._qrcode_id:
            log_info("[WeChat Auth] No QR code requested — returning early")
            return {"authenticated": self._authenticated, "error": "No QR code requested"}

        if not self._poll_lock.acquire(blocking=False):
            log_info("[WeChat Auth] Poll already in progress — returning cached result")
            if self._last_poll_result is not None:
                return self._last_poll_result.copy()
            return {"authenticated": False}

        try:
            data = self._client.get_qrcode_status(self._qrcode_id)
        except Exception as e:
            log_error(f"[WeChat Auth] Status check failed: {e}")
            result = {"authenticated": False, "error": str(e)}
            self._last_poll_result = result
            return result
        finally:
            self._poll_lock.release()

        status = data.get("status", "")
        bot_token = data.get("bot_token", "")
        log_info(f"[WeChat Auth] QR status={status!r}, has_bot_token={bool(bot_token)}")

        if status in ("confirmed", "success") and bot_token:
            self._authenticated = True
            self._qrcode_id = None
            self._client.set_bot_token(bot_token)

            # Persist session
            save_session(
                channel_type="wechat",
                bot_token=bot_token,
                status="authenticated",
            )

            # Fetch server config and persist
            try:
                config_data = self._client.getconfig()
                save_session(
                    channel_type="wechat",
                    bot_token=bot_token,
                    config_data=config_data,
                    status="authenticated",
                )
            except Exception as e:
                log_error(f"[WeChat Auth] Failed to fetch config: {e}")

            log_info("[WeChat Auth] Authentication successful!")
            result = {
                "authenticated": True,
                "bot_token": bot_token,
                "status": status,
            }
            self._last_poll_result = result
            return result

        result = {
            "authenticated": False,
            "status": status,
        }
        self._last_poll_result = result
        return result

    def logout(self) -> bool:
        """Clear the current session."""
        self._qrcode_id = None
        self._qrcode_img_content = None
        self._authenticated = False
        self._client.set_bot_token("")
        from imBots.store import delete_session
        delete_session("wechat")
        log_info("[WeChat Auth] Logged out")
        return True

    def restore_from_store(self) -> bool:
        """Try to restore a previously saved session from the store."""
        session = get_session("wechat")
        if session and session.get("bot_token"):
            self._client.set_bot_token(session["bot_token"])
            self._authenticated = True
            log_info("[WeChat Auth] Session restored from store")
            return True
        return False
