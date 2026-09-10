"""Unit tests for imChannels.wechat.ilink_client ILinkBotsClient."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from imChannels.wechat.ilink_client import (
    ILinkBotsClient,
    ILINK_BASE_URL,
    UPLOAD_MEDIA_TYPE_FILE,
)


class TestTokenManagement:
    def test_default_token_empty(self):
        client = ILinkBotsClient()
        assert client.bot_token == ""

    def test_set_bot_token(self):
        client = ILinkBotsClient()
        client.set_bot_token("my_token")
        assert client.bot_token == "my_token"

    def test_init_with_token(self):
        client = ILinkBotsClient(bot_token="init_tok")
        assert client.bot_token == "init_tok"

    def test_get_updates_buf_default_empty(self):
        client = ILinkBotsClient()
        assert client.get_updates_buf == ""

    def test_get_updates_buf_setter(self):
        client = ILinkBotsClient()
        client.get_updates_buf = "cursor123"
        assert client.get_updates_buf == "cursor123"


class TestProxy:
    def test_no_proxy_returns_none(self):
        client = ILinkBotsClient()
        assert client._proxy_dict() is None

    def test_proxy_returns_dict(self):
        client = ILinkBotsClient(proxy="http://proxy:8080")
        d = client._proxy_dict()
        assert d["http"] == "http://proxy:8080"
        assert d["https"] == "http://proxy:8080"


class TestPostJson:
    @patch("imChannels.wechat.ilink_client.requests.Session")
    def test_post_json_sends_json_with_auth_headers(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient(bot_token="tok123")
        result = client._post_json("test_endpoint", {"data": "val"})

        assert result == {"ok": True}
        call_args = mock_session.post.call_args
        assert call_args[0][0] == f"{ILINK_BASE_URL}/test_endpoint"

        # Verify JSON body (not form-encoded)
        assert call_args[1]["json"]["data"] == "val"
        assert call_args[1]["json"]["base_info"]["channel_version"] == "1.0.2"
        assert "bot_token" not in call_args[1]["json"]

        # Verify auth headers
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer tok123"
        assert headers["AuthorizationType"] == "ilink_bot_token"
        assert headers["Content-Type"] == "application/json"
        assert "X-WECHAT-UIN" in headers

    @patch("imChannels.wechat.ilink_client.requests.Session")
    def test_post_json_no_token_skips_auth(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient()
        client._post_json("ep")
        headers = mock_session.post.call_args[1]["headers"]
        assert "Authorization" not in headers

    @patch("imChannels.wechat.ilink_client.requests.Session")
    def test_post_json_raises_on_request_error(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.ConnectionError("fail")
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient()
        with pytest.raises(requests.ConnectionError):
            client._post_json("ep")

    @patch("imChannels.wechat.ilink_client.requests.Session")
    def test_post_json_raises_on_json_error(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("err", "", 0)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient()
        with pytest.raises(json.JSONDecodeError):
            client._post_json("ep")


class TestGetHelper:
    @patch("imChannels.wechat.ilink_client.requests.Session")
    def test_get_sends_get_request(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"qrcode": "abc"}
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient()
        result = client._get("get_bot_qrcode?bot_type=3")

        assert result == {"qrcode": "abc"}
        call_args = mock_session.get.call_args
        assert call_args[0][0] == f"{ILINK_BASE_URL}/get_bot_qrcode?bot_type=3"


class TestAPIEndpoints:
    @patch.object(ILinkBotsClient, "_get")
    def test_get_bot_qrcode(self, mock_get):
        mock_get.return_value = {"qrcode": "abc123", "qrcode_img_content": "http://img"}
        client = ILinkBotsClient()
        result = client.get_bot_qrcode()
        assert result["qrcode"] == "abc123"
        mock_get.assert_called_once_with("get_bot_qrcode?bot_type=3")

    @patch.object(ILinkBotsClient, "_get")
    def test_get_qrcode_status(self, mock_get):
        mock_get.return_value = {"status": "scanned"}
        result = ILinkBotsClient().get_qrcode_status("qr1")
        mock_get.assert_called_once_with("get_qrcode_status?qrcode=qr1", quiet=True)
        assert result["status"] == "scanned"

    @patch.object(ILinkBotsClient, "_post_json")
    def test_getupdates_sends_cursor(self, mock_post):
        mock_post.return_value = {"ret": 0, "msgs": [{"msg_id": "1"}], "get_updates_buf": "buf123"}
        client = ILinkBotsClient()
        result = client.getupdates(timeout=30)
        mock_post.assert_called_once_with(
            "getupdates",
            {"get_updates_buf": ""},
            timeout=35,
        )
        assert len(result["msgs"]) == 1
        assert client.get_updates_buf == "buf123"

    @patch.object(ILinkBotsClient, "_post_json")
    def test_getupdates_preserves_existing_cursor(self, mock_post):
        mock_post.return_value = {"ret": 0, "msgs": [], "get_updates_buf": "new_cursor"}
        client = ILinkBotsClient()
        client.get_updates_buf = "old_cursor"
        client.getupdates()
        mock_post.assert_called_once_with(
            "getupdates",
            {"get_updates_buf": "old_cursor"},
            timeout=40,
        )
        assert client.get_updates_buf == "new_cursor"

    @patch.object(ILinkBotsClient, "_post_json")
    def test_sendmessage(self, mock_post):
        mock_post.return_value = {"sent": True}
        client = ILinkBotsClient(bot_token="tok")
        result = client.sendmessage(
            to_user_id="user@im.wechat",
            content="hello",
            context_token="ct1",
        )
        call_payload = mock_post.call_args[0][1]
        assert call_payload["msg"]["to_user_id"] == "user@im.wechat"
        assert call_payload["msg"]["context_token"] == "ct1"
        assert call_payload["msg"]["item_list"] == [{"type": 1, "text_item": {"text": "hello"}}]
        assert call_payload["msg"]["message_type"] == 2
        assert call_payload["msg"]["message_state"] == 2
        assert result["sent"] is True

    @patch.object(ILinkBotsClient, "_post_json")
    def test_getconfig(self, mock_post):
        mock_post.return_value = {"typing_ticket": "ticket123"}
        ILinkBotsClient().getconfig(to_user_id="user@im.wechat", context_token="ct1")
        call_payload = mock_post.call_args[0][1]
        assert call_payload["to_user_id"] == "user@im.wechat"
        assert call_payload["context_token"] == "ct1"

    @patch.object(ILinkBotsClient, "_post_json")
    def test_getconfig_no_args(self, mock_post):
        mock_post.return_value = {"key": "val"}
        ILinkBotsClient().getconfig()
        call_payload = mock_post.call_args[0][1]
        assert "to_user_id" not in call_payload

    @patch.object(ILinkBotsClient, "_post_json")
    def test_sendtyping(self, mock_post):
        mock_post.return_value = {}
        ILinkBotsClient().sendtyping(
            to_user_id="user@im.wechat",
            typing_ticket="ticket123",
            status=1,
        )
        call_payload = mock_post.call_args[0][1]
        assert call_payload["to_user_id"] == "user@im.wechat"
        assert call_payload["typing_ticket"] == "ticket123"
        assert call_payload["status"] == 1

    @patch.object(ILinkBotsClient, "_post_json")
    def test_getuploadurl(self, mock_post):
        mock_post.return_value = {
            "ret": 0,
            "thumb_upload_param": "",
            "upload_param": "AAFBdGlja2V0MTIzNDU2Nzg5MA==",
        }
        result = ILinkBotsClient().getuploadurl(
            filekey="7623583ed1d84f8284d8b003905b8e04",
            media_type=UPLOAD_MEDIA_TYPE_FILE,
            to_user_id="user@im.wechat",
            raw_size=100,
            encrypted_size=112,
            aes_key=b"\x00\x11\x22\x33\x44\x55\x66\x77\x88\x99\xaa\xbb\xcc\xdd\xee\xff",
            raw_file_md5="9c4d5c0b21f7f5c77c2b12f05f1b8df8",
        )
        call_payload = mock_post.call_args[0][1]
        assert call_payload["media_type"] == 3  # getuploadurl 编码: FILE
        assert call_payload["filekey"] == "7623583ed1d84f8284d8b003905b8e04"
        assert call_payload["rawsize"] == 100
        assert call_payload["filesize"] == 112
        assert call_payload["rawfilemd5"] == "9c4d5c0b21f7f5c77c2b12f05f1b8df8"
        assert call_payload["aeskey"] == "00112233445566778899aabbccddeeff"  # hex, 非 base64
        assert call_payload["no_need_thumb"] is True
        assert result["upload_param"] == "AAFBdGlja2V0MTIzNDU2Nzg5MA=="
