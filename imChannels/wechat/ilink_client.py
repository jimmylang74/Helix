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
import hashlib
import json
import os
import secrets
import uuid
from typing import Any, Dict, Optional

import requests

from modules.utils.logger import log_error, log_info, log_warning
from imChannels.wechat.crypto import (
    aes_decrypt,
    aes_encrypt,
    decode_aes_key,
)

# ── iLink API base URL ─────────────────────────────────────────────────────

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com/ilink/bot"

# Media CDN base URL for upload/download of encrypted media payloads.
ILINK_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

# Media type codes used in item_list / getuploadurl.
MEDIA_TYPE_TEXT = 1
MEDIA_TYPE_IMAGE = 2
MEDIA_TYPE_VOICE = 3
MEDIA_TYPE_FILE = 4
MEDIA_TYPE_VIDEO = 5

CHANNEL_VERSION = "1.0.2"

ILINK_ERRCODE_TOKEN_INVALID = -14


def _random_wechat_uin() -> str:
    """Generate X-WECHAT-UIN: random uint32 → decimal string → base64."""
    value = secrets.randbelow(2 ** 32)
    return base64.b64encode(str(value).encode("utf-8")).decode("utf-8")


def parse_media_item(item: Dict[str, Any], cdn_base: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Extract a downloadable media descriptor from an iLink ``item_list`` entry.

    Returns a dict with ``key``, ``type`` (2=image,3=voice,4=file,5=video),
    ``media_id``, ``aes_key``, ``url`` and the raw ``media`` blob — or ``None``
    when the item carries no downloadable media. Media items expose their
    encrypted payload via ``media`` (with ``url`` / ``encrypt_query_param`` /
    ``aes_key``) or a top-level ``media_id`` plus ``aes_key``.
    """
    item_type = item.get("type")
    if item_type not in (MEDIA_TYPE_IMAGE, MEDIA_TYPE_VOICE, MEDIA_TYPE_FILE, MEDIA_TYPE_VIDEO):
        return None

    # media may live at item level OR nested inside file_item/voice_item
    media = (
        item.get("media")
        or item.get("file_item", {}).get("media")
        or item.get("voice_item", {}).get("media")
        or {}
    )
    url = media.get("url") or media.get("full_url") or ""
    aes_key = (
        media.get("aes_key")
        or media.get("encrypt_aes_key")
        or item.get("aes_key")
        or ""
    )
    media_id = (
        media.get("media_id")
        or media.get("file_id")
        or item.get("media_id")
        or ""
    )

    # Fall back to standard download URL if the media blob lacks one.
    if not url and media_id:
        base = cdn_base or ILINK_CDN_BASE_URL
        url = f"{base}/download?media_id={media_id}"

    if not url or not aes_key:
        return None

    key = (
        media.get("filekey")
        or media.get("file_name")
        or media.get("file_id")
        or item.get("file_item", {}).get("file_name")
        or f"media_{item_type}_{media_id or 'unknown'}"
    )
    return {
        "key": key,
        "type": item_type,
        "media_id": media_id,
        "aes_key": aes_key,
        "url": url,
        "media": media,
    }


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

    def getuploadurl(
        self,
        filekey: str,
        media_type: int,
        to_user_id: str,
        file_size: int,
        aes_key: bytes,
        file_md5: Optional[str] = None,
        raw_file_md5: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Request an upload URL + media_id for sending a media file.

        A successful response carries the CDN ``upload_param`` (a signed
        upload ticket) and a ``media_id`` that can be referenced in a
        subsequent ``sendmessage`` FILE/VOICE item.

        Args:
            filekey: Stable file key / filename identifying the media.
            media_type: 2=image, 3=voice, 4=file, 5=video.
            to_user_id: The receiving user.
            file_size: Size of the (raw, pre-encryption) payload in bytes.
            aes_key: 16-byte key used to encrypt the payload for the CDN.
            file_md5: md5 of the *encrypted* payload that will be uploaded.
            raw_file_md5: md5 of the *raw* payload.
        """
        payload: Dict[str, Any] = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": file_size,
            "rawfilemd5": raw_file_md5 or "",
            "filesize": file_size,
            "aeskey": base64.b64encode(aes_key).decode("utf-8"),
            "no_need_thumb": True,
        }
        if file_md5:
            payload["filemd5"] = file_md5
        payload.update(kwargs)
        data = self._post_json("getuploadurl", payload)
        log_info(f"[iLink] getuploadurl: filekey={filekey}, media_type={media_type}, keys={list(data.keys())}")
        return data

    # ── Media CDN transfer ─────────────────────────────────────────────

    @staticmethod
    def _md5(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def upload_media(
        self,
        upload_param: Dict[str, Any],
        aes_key: bytes,
        raw_data: bytes,
    ) -> Dict[str, Any]:
        """Encrypt ``raw_data`` and upload it to the media CDN.

        ``upload_param`` is the signed ticket returned by ``getuploadurl``;
        the CDN expects it (plus an ``aes_key`` field) posted together with
        the AES-encrypted payload as raw octet-stream body.

        Returns:
            The CDN JSON response (``errcode``/``ret`` 0 on success).
        """
        url = upload_param.get("url") or f"{ILINK_CDN_BASE_URL}/upload"
        body = dict(upload_param)
        body["aeskey"] = base64.b64encode(aes_key).decode("utf-8")
        encrypted = aes_encrypt(raw_data, aes_key)

        headers = {
            "Content-Type": "application/octet-stream",
            "aeskey": body["aeskey"],
        }
        try:
            resp = self._session.post(
                url,
                params=body,
                data=encrypted,
                headers=headers,
                timeout=120,
                proxies=self._proxy_dict(),
            )
            resp.raise_for_status()
            data = resp.json()
            log_info(f"[iLink] Media uploaded: {len(encrypted)} bytes, keys={list(data.keys())}")
            return data
        except requests.RequestException as e:
            log_error(f"[iLink] Media upload failed: {e}")
            raise

    def download_media(self, media_info: Dict[str, Any], file_path: str) -> str:
        """Download and decrypt an incoming media item to ``file_path``.

        ``media_info`` is the parsed media descriptor extracted from a
        getupdates item (see ``parse_media_item``) containing at least
        ``url`` (a <scheme>://<host>/.../download?...> CDN URL carrying the
        media id / encrypt query params) and ``aes_key``.

        Returns the absolute path written on success; raises on failure.
        """
        url = media_info["url"]
        aes_key = decode_aes_key(media_info["aes_key"])
        headers = {"aeskey": base64.b64encode(aes_key).decode("utf-8")}

        try:
            resp = self._session.get(
                url,
                headers=headers,
                timeout=120,
                proxies=self._proxy_dict(),
            )
            resp.raise_for_status()
            encrypted = resp.content
        except requests.RequestException as e:
            log_error(f"[iLink] Media download request failed: {e}")
            raise

        raw = aes_decrypt(encrypted, aes_key)
        directory = os.path.dirname(file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(raw)
        log_info(f"[iLink] Media downloaded to {file_path} ({len(raw)} bytes)")
        return file_path

    def send_media(
        self,
        to_user_id: str,
        context_token: str,
        item_list: Any,
        **kwargs,
    ) -> Dict[str, Any]:
        """Send a ``sendmessage`` carrying arbitrary ``item_list`` media items.

        The items reference a previously obtained ``media_id`` (from
        ``getuploadurl``); media_type must be set to match the item type.
        """
        client_id = f"hl-{uuid.uuid4().hex[:12]}"
        msg: Dict[str, Any] = {
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": item_list,
        }
        msg.update(kwargs)
        data = self._post_json("sendmessage", {"msg": msg})
        log_info(f"[iLink] Media message sent to={to_user_id[:16]}..., client_id={client_id}")
        return data
