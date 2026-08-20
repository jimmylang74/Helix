"""
WeChat iLinkBot protocol HTTP client.

Wraps the 7 official WeChat iLink API endpoints:
  get_bot_qrcode, get_qrcode_status, getupdates, sendmessage,
  getconfig, sendtyping, getuploadurl

Protocol reference:
  - Content-Type: application/json
  - Authorization: Bearer <bot_token>
  - AuthorizationType: ilink_bot_token
  - X-WECHAT-UIN: base64(random_uint32) per request
  - Every POST body includes base_info: {"channel_version": "1.0.2"}
"""

import base64
import json
import secrets
import uuid
from typing import Any, Dict, Optional

import requests

from modules.utils.logger import log_error, log_info, log_warning

# ── iLink API base URL ─────────────────────────────────────────────────────

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com/ilink/bot"

CHANNEL_VERSION = "1.0.2"

ILINK_ERRCODE_TOKEN_INVALID = -14


def _random_wechat_uin() -> str:
    """Generate X-WECHAT-UIN: random uint32 → decimal string → base64."""
    value = secrets.randbelow(2 ** 32)
    return base64.b64encode(str(value).encode("utf-8")).decode("utf-8")


class ILinkBotsClient:
    """HTTP client for the WeChat iLinkBot protocol (ChannelClient)."""

    def __init__(self, bot_token: str = "", proxy: Optional[str] = None):
        self._bot_token = bot_token
        self._proxy = proxy
        self._session = requests.Session()
        # Default headers — overridden per-request with fresh X-WECHAT-UIN
        self._session.headers.update({
            "Accept": "application/json",
        })
        self._get_updates_buf: str = ""

    # ── Token management ───────────────────────────────────────────────

    def set_bot_token(self, token: str) -> None:
        self._bot_token = token

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @property
    def get_updates_buf(self) -> str:
        return self._get_updates_buf

    @get_updates_buf.setter
    def get_updates_buf(self, value: str) -> None:
        self._get_updates_buf = value

    @staticmethod
    def is_token_error(data: Dict[str, Any]) -> bool:
        return data.get("errcode") == ILINK_ERRCODE_TOKEN_INVALID

    def _proxy_dict(self) -> Optional[Dict[str, str]]:
        if self._proxy:
            return {"http": self._proxy, "https": self._proxy}
        return None

    # ── HTTP helpers ───────────────────────────────────────────────────

    def _auth_headers(self) -> Dict[str, str]:
        """Build per-request headers with fresh X-WECHAT-UIN and auth token."""
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }
        if self._bot_token:
            headers["Authorization"] = f"Bearer {self._bot_token}"
        return headers

    def _post_json(self, endpoint: str, payload: Optional[Dict[str, Any]] = None,
                   timeout: int = 30, quiet: bool = False) -> Dict[str, Any]:
        """POST JSON to an iLink endpoint with proper auth headers."""
        url = f"{ILINK_BASE_URL}/{endpoint}"
        body = dict(payload or {})
        body["base_info"] = {"channel_version": CHANNEL_VERSION}

        try:
            resp = self._session.post(
                url,
                json=body,
                headers=self._auth_headers(),
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

    def _get(self, path: str, timeout: int = 15, quiet: bool = False) -> Dict[str, Any]:
        """GET request (used for unauthenticated QR code endpoints)."""
        url = f"{ILINK_BASE_URL}/{path}"
        try:
            resp = self._session.get(
                url,
                timeout=timeout,
                proxies=self._proxy_dict(),
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if not quiet:
                log_error(f"iLink GET {path} failed: {e}")
            raise
        except json.JSONDecodeError as e:
            log_error(f"iLink GET {path} invalid JSON: {e}")
            raise

    # ── API Methods ────────────────────────────────────────────────────

    def get_bot_qrcode(self) -> Dict[str, Any]:
        """Request a QR code for bot login (GET, no auth required).

        Returns::

            {
                "qrcode": "<qrcode_id>",
                "qrcode_img_content": "<url_to_qr_image>"
            }
        """
        log_info("[iLink] Requesting bot QR code...")
        data = self._get("get_bot_qrcode?bot_type=3")
        log_info(f"[iLink] QR code received, id={data.get('qrcode', '')[:16]}...")
        return data

    def get_qrcode_status(self, qrcode: str) -> Dict[str, Any]:
        """Poll QR code scan status (GET, no auth required).

        Returns fields including ``status`` (e.g. "wait", "scaned", "confirmed")
        and ``bot_token`` once confirmed.
        """
        data = self._get(f"get_qrcode_status?qrcode={qrcode}", quiet=True)
        return data

    def getupdates(self, timeout: int = 35) -> Dict[str, Any]:
        """Long-poll for new messages.

        Uses ``get_updates_buf`` as a cursor — the server returns a new
        cursor in each response which must be echoed back in the next request.

        Returns::

            {
                "ret": 0,
                "get_updates_buf": "<cursor>",
                "msgs": [...]
            }
        """
        data = self._post_json(
            "getupdates",
            {"get_updates_buf": self._get_updates_buf},
            timeout=timeout + 5,
        )

        # Update cursor from response
        new_buf = data.get("get_updates_buf", "")
        if new_buf:
            self._get_updates_buf = new_buf

        errcode = data.get("errcode") or data.get("ret")
        if errcode is not None and errcode != 0:
            if self.is_token_error(data):
                log_error(f"[iLink] getupdates: bot_token invalid (errcode={errcode}, errmsg={data.get('errmsg', '')})")
            else:
                log_error(f"[iLink] getupdates error: errcode={errcode}, errmsg={data.get('errmsg', '')}, keys={list(data.keys())}")
        elif not data.get("msgs"):
            log_info(f"[iLink] getupdates: 0 msgs, response keys={list(data.keys())}")

        return data

    def sendmessage(
        self,
        to_user_id: str,
        content: str,
        context_token: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """Send a message using the iLink protocol format.

        Payload::

            {
                "msg": {
                    "to_user_id": "...",
                    "client_id": "hl-<uuid>",
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": "...",
                    "item_list": [{"type": 1, "text_item": {"text": "..."}}]
                },
                "base_info": {"channel_version": "1.0.2"}
            }
        """
        client_id = f"hl-{uuid.uuid4().hex[:12]}"
        msg: Dict[str, Any] = {
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": [{"type": 1, "text_item": {"text": content}}],
        }
        msg.update(kwargs)
        data = self._post_json("sendmessage", {"msg": msg})
        log_info(f"[iLink] Message sent to={to_user_id[:16]}..., client_id={client_id}")
        return data

    def getconfig(self, to_user_id: str = "", context_token: str = "") -> Dict[str, Any]:
        """Fetch server-side bot configuration (typing_ticket).

        Args:
            to_user_id: The user ID to get config for.
            context_token: The conversation context token.
        """
        payload: Dict[str, Any] = {}
        if to_user_id:
            payload["to_user_id"] = to_user_id
        if context_token:
            payload["context_token"] = context_token
        data = self._post_json("getconfig", payload)
        log_info(f"[iLink] Config received: {list(data.keys())}")
        return data

    def sendtyping(self, to_user_id: str, typing_ticket: str, status: int = 1,
                   **kwargs) -> Dict[str, Any]:
        """Send 'typing' indicator.

        Args:
            to_user_id: The user to show typing for.
            typing_ticket: From getconfig response.
            status: 1=start typing, 2=stop typing.
        """
        payload: Dict[str, Any] = {
            "to_user_id": to_user_id,
            "typing_ticket": typing_ticket,
            "status": status,
        }
        payload.update(kwargs)
        return self._post_json("sendtyping", payload)

    def getuploadurl(self, **kwargs) -> Dict[str, Any]:
        """Get an upload URL for media (images, files)."""
        return self._post_json("getuploadurl", kwargs or {})
