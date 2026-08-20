"""
WeChat iLinkBot protocol HTTP client.

Wraps the 7 official WeChat iLink API endpoints:
  get_bot_qrcode, get_qrcode_status, getupdates, sendmessage,
  getconfig, sendtyping, getuploadurl
"""

import json
from typing import Any, Dict, Optional

import requests

from modules.utils.logger import log_error, log_info

# ── iLink API base URL ─────────────────────────────────────────────────────

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com/ilink/bot"


class ILinkBotsClient:
    """HTTP client for the WeChat iLinkBot protocol (ChannelClient)."""

    def __init__(self, bot_token: str = "", proxy: Optional[str] = None):
        self._bot_token = bot_token
        self._proxy = proxy
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
        })

    # ── Token management ───────────────────────────────────────────────

    def set_bot_token(self, token: str) -> None:
        self._bot_token = token

    @property
    def bot_token(self) -> str:
        return self._bot_token

    def _proxy_dict(self) -> Optional[Dict[str, str]]:
        if self._proxy:
            return {"http": self._proxy, "https": self._proxy}
        return None

    def _post(self, endpoint: str, payload: Optional[Dict[str, Any]] = None,
              timeout: int = 30, quiet: bool = False) -> Dict[str, Any]:
        """POST to an iLink endpoint and return the JSON response."""
        url = f"{ILINK_BASE_URL}/{endpoint}"
        body = payload or {}
        if self._bot_token:
            body["bot_token"] = self._bot_token

        try:
            resp = self._session.post(
                url,
                data=body,
                timeout=timeout,
                proxies=self._proxy_dict(),
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if not quiet:
                log_error(f"iLink {endpoint} request failed: {e}")
            raise
        except json.JSONDecodeError as e:
            log_error(f"iLink {endpoint} invalid JSON response: {e}")
            raise

    # ── API Methods ────────────────────────────────────────────────────

    def get_bot_qrcode(self) -> Dict[str, Any]:
        """Request a QR code for bot login.

        Returns::

            {
                "qrcode": "<qrcode_id>",
                "qrcode_img_content": "<url_to_qr_image>"
            }
        """
        log_info("[iLink] Requesting bot QR code...")
        data = self._post("get_bot_qrcode", {"bot_type": 3})
        log_info(f"[iLink] QR code received, id={data.get('qrcode', '')[:16]}...")
        return data

    def get_qrcode_status(self, qrcode: str) -> Dict[str, Any]:
        """Poll QR code scan status.

        Returns fields including ``status`` (e.g. "waiting", "scanned", "confirmed")
        and ``bot_token`` once confirmed.
        """
        data = self._post("get_qrcode_status", {"qrcode": qrcode}, quiet=True)
        return data

    def getupdates(self, timeout: int = 50) -> Dict[str, Any]:
        """Long-poll for new messages.

        Returns::

            {
                "updates": [
                    {
                        "msg_id": "...",
                        "from_user": "...",
                        "content": "...",
                        "msg_type": "text",
                        "context_token": "...",
                        ...
                    }
                ]
            }
        """
        data = self._post("getupdates", {"timeout": timeout}, timeout=timeout + 10)
        return data

    def sendmessage(
        self,
        context_token: str,
        content: str,
        msg_type: str = "text",
        **kwargs,
    ) -> Dict[str, Any]:
        """Send a message using a context_token for routing."""
        payload: Dict[str, Any] = {
            "context_token": context_token,
            "content": content,
            "msg_type": msg_type,
        }
        payload.update(kwargs)
        data = self._post("sendmessage", payload)
        log_info(f"[iLink] Message sent via context_token={context_token[:16]}...")
        return data

    def getconfig(self) -> Dict[str, Any]:
        """Fetch server-side bot configuration."""
        data = self._post("getconfig")
        log_info(f"[iLink] Config received: {list(data.keys())}")
        return data

    def sendtyping(self, **kwargs) -> Dict[str, Any]:
        """Send 'typing' indicator."""
        return self._post("sendtyping", kwargs or {})

    def getuploadurl(self, **kwargs) -> Dict[str, Any]:
        """Get an upload URL for media (images, files, etc.)."""
        return self._post("getuploadurl", kwargs or {})
