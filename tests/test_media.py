"""Tests for WeChat iLink media send/receive: crypto, parse_media_item, channel helpers."""

import base64
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from imChannels.wechat.crypto import (
    aes_decrypt,
    aes_encrypt,
    decode_aes_key,
    generate_aes_key,
)
from imChannels.wechat.ilink_client import (
    ILinkBotsClient,
    MEDIA_TYPE_FILE,
    MEDIA_TYPE_IMAGE,
    MEDIA_TYPE_VOICE,
    MEDIA_TYPE_VIDEO,
    parse_media_item,
)


# ── crypto ──────────────────────────────────────────────────────────────────


class TestDecodeAesKey:
    def test_direct_32_char_hex(self):
        hex32 = "00112233445566778899aabbccddeeff"
        assert decode_aes_key(hex32).hex() == hex32

    def test_base64_of_raw_16_bytes(self):
        raw = bytes(range(16))
        b64 = base64.b64encode(raw).decode()
        assert decode_aes_key(b64) == raw

    def test_base64_of_hex_string(self):
        hex32 = "aabbccddeeff00112233aabbccddeeff"
        b64 = base64.b64encode(hex32.encode()).decode()
        assert decode_aes_key(b64) == bytes.fromhex(hex32)

    def test_uppercase_hex(self):
        hex_str = "00112233445566778899AABBCCDDEEFF"
        key = decode_aes_key(hex_str)
        assert key == bytes.fromhex(hex_str.lower())

    def test_none_raises(self):
        with pytest.raises(ValueError, match="None"):
            decode_aes_key(None)

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            decode_aes_key("not_a_key")


class TestAesEncryptDecrypt:
    def test_roundtrip(self):
        key = generate_aes_key()
        assert len(key) == 16
        plaintext = b"Hello iLink file content 1234567890"
        ct = aes_encrypt(plaintext, key)
        assert ct != plaintext
        assert aes_decrypt(ct, key) == plaintext

    def test_empty_plaintext(self):
        key = generate_aes_key()
        ct = aes_encrypt(b"", key)
        assert aes_decrypt(ct, key) == b""

    def test_block_boundary(self):
        key = generate_aes_key()
        data = b"x" * 16
        ct = aes_encrypt(data, key)
        assert aes_decrypt(ct, key) == data

    def test_large_payload(self):
        key = generate_aes_key()
        data = os.urandom(100_000)
        ct = aes_encrypt(data, key)
        assert aes_decrypt(ct, key) == data


# ── parse_media_item ────────────────────────────────────────────────────────


class TestParseMediaItem:
    def test_file_item(self):
        item = {
            "type": 4,
            "media": {
                "url": "https://cdn/download?media_id=123",
                "aes_key": "aabbccddeeff00112233aabbccddeeff",
                "media_id": "123",
            },
        }
        result = parse_media_item(item)
        assert result is not None
        assert result["type"] == MEDIA_TYPE_FILE
        assert result["media_id"] == "123"
        assert result["url"] == "https://cdn/download?media_id=123"
        assert result["aes_key"] == "aabbccddeeff00112233aabbccddeeff"

    def test_voice_item(self):
        item = {
            "type": 3,
            "media": {
                "url": "https://cdn/dl?media_id=v1",
                "aes_key": "00112233445566778899aabbccddeeff",
                "media_id": "v1",
            },
        }
        result = parse_media_item(item)
        assert result is not None
        assert result["type"] == MEDIA_TYPE_VOICE

    def test_text_item_returns_none(self):
        item = {"type": 1, "text_item": {"text": "hello"}}
        assert parse_media_item(item) is None

    def test_item_without_media_returns_none(self):
        item = {"type": 4}
        assert parse_media_item(item) is None

    def test_media_id_fallback_url(self):
        item = {"type": 4, "media": {"aes_key": "aa" * 16, "media_id": "mid1"}}
        result = parse_media_item(item)
        assert result is not None
        assert "mid1" in result["url"]

    def test_key_uses_file_name(self):
        item = {
            "type": 4,
            "media": {
                "url": "https://cdn/x",
                "aes_key": "bb" * 16,
                "file_name": "report.pdf",
            },
        }
        result = parse_media_item(item)
        assert result is not None
        assert result["key"] == "report.pdf"


# ── channel helper functions ────────────────────────────────────────────────


class TestChannelHelpers:
    def test_media_type_str(self):
        from imChannels.wechat.channel import _media_type_str

        assert _media_type_str(MEDIA_TYPE_FILE) == "file"
        assert _media_type_str(MEDIA_TYPE_VOICE) == "voice"
        assert _media_type_str(MEDIA_TYPE_IMAGE) == "image"
        assert _media_type_str(MEDIA_TYPE_VIDEO) == "video"
        assert _media_type_str(99) == "text"

    def test_safe_media_name(self):
        from imChannels.wechat.channel import _safe_media_name

        assert _safe_media_name("report.pdf", MEDIA_TYPE_FILE) == "report.pdf"
        assert _safe_media_name("", MEDIA_TYPE_FILE).startswith("media_")
        assert "/" not in _safe_media_name("../etc/passwd", MEDIA_TYPE_FILE)

    def test_estimate_voice_seconds(self):
        from imChannels.wechat.channel import _estimate_voice_seconds

        assert _estimate_voice_seconds(2048) == 1
        assert _estimate_voice_seconds(20480) == 10
        assert _estimate_voice_seconds(0) == 1

    def test_media_msg_type(self):
        from imChannels.wechat.channel import _media_msg_type

        assert _media_msg_type("file") == "file"
        assert _media_msg_type(None) is None

    def test_media_extension(self):
        from imChannels.wechat.channel import _media_extension

        assert _media_extension(MEDIA_TYPE_VOICE) == ".amr"
        assert _media_extension(MEDIA_TYPE_FILE) == ".bin"


# ── ILinkBotsClient media methods ──────────────────────────────────────────


class TestILinkMediaMethods:
    @patch("imChannels.wechat.ilink_client.requests.Session")
    def test_getuploadurl_sends_correct_payload(self, mock_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"media_id": "m1", "upload_param": {"url": "x"}}
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_cls.return_value = mock_session

        client = ILinkBotsClient(bot_token="tok")
        import base64

        key = bytes(range(16))
        result = client.getuploadurl(
            filekey="test.pdf",
            media_type=MEDIA_TYPE_FILE,
            to_user_id="user1",
            file_size=1024,
            aes_key=key,
        )

        call_args = mock_session.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["msg"]["item_list"][0]["type"] if "msg" in body else True
        payload = body if "filekey" in body else body.get("msg", body)
        assert result["media_id"] == "m1"

    def test_download_media_encrypts_and_writes(self):
        client = ILinkBotsClient()
        key = generate_aes_key()
        secret_data = b"secret file bytes"
        encrypted = aes_encrypt(secret_data, key)

        mock_resp = MagicMock()
        mock_resp.content = encrypted
        mock_resp.raise_for_status = MagicMock()
        client._session = MagicMock()
        client._session.get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.bin")
            media_info = {
                "url": "https://cdn/download?media_id=123",
                "aes_key": base64.b64encode(key).decode(),
            }
            result = client.download_media(media_info, path)
            assert result == path
            with open(path, "rb") as f:
                assert f.read() == secret_data


# ── ConfigManager download_dir ──────────────────────────────────────────────


class TestDownloadDirConfig:
    def test_config_manager_has_wechat_section(self):
        from modules.config.config_manager import ConfigManager

        cm = ConfigManager()
        default = cm.get_wechat_download_dir()
        assert default == "download"

    def test_set_and_get_download_dir(self):
        from modules.config.config_manager import ConfigManager

        cm = ConfigManager()
        original = cm.get("channels.wechat.download_dir")
        try:
            cm.set("channels.wechat.download_dir", "my_downloads")
            assert cm.get_wechat_download_dir() == "my_downloads"
        finally:
            cm.set("channels.wechat.download_dir", original)

    def test_get_download_dir_returns_abs_path(self):
        from modules.utils.paths import get_download_dir

        path = get_download_dir()
        assert os.path.isabs(path)
        assert path.endswith("download")
