"""Unit tests for imBots.wechat.ilink_client ILinkBotsClient."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from imBots.wechat.ilink_client import ILinkBotsClient, ILINK_BASE_URL


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


class TestProxy:
    def test_no_proxy_returns_none(self):
        client = ILinkBotsClient()
        assert client._proxy_dict() is None

    def test_proxy_returns_dict(self):
        client = ILinkBotsClient(proxy="http://proxy:8080")
        d = client._proxy_dict()
        assert d["http"] == "http://proxy:8080"
        assert d["https"] == "http://proxy:8080"


class TestPost:
    @patch("imBots.wechat.ilink_client.requests.Session")
    def test_post_includes_bot_token(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient(bot_token="tok123")
        result = client._post("test_endpoint", {"data": "val"})

        assert result == {"ok": True}
        call_args = mock_session.post.call_args
        assert call_args[0][0] == f"{ILINK_BASE_URL}/test_endpoint"
        body = call_args[1]["data"]
        assert body["bot_token"] == "tok123"
        assert body["data"] == "val"

    @patch("imBots.wechat.ilink_client.requests.Session")
    def test_post_no_token_skips_field(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient()
        client._post("ep")
        body = mock_session.post.call_args[1]["data"]
        assert "bot_token" not in body

    @patch("imBots.wechat.ilink_client.requests.Session")
    def test_post_raises_on_request_error(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.ConnectionError("fail")
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient()
        with pytest.raises(requests.ConnectionError):
            client._post("ep")

    @patch("imBots.wechat.ilink_client.requests.Session")
    def test_post_raises_on_json_error(self, mock_session_cls):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("err", "", 0)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        client = ILinkBotsClient()
        with pytest.raises(json.JSONDecodeError):
            client._post("ep")


class TestAPIEndpoints:
    @patch.object(ILinkBotsClient, "_post")
    def test_get_bot_qrcode(self, mock_post):
        mock_post.return_value = {"qrcode": "abc123", "qrcode_img_content": "http://img"}
        client = ILinkBotsClient()
        result = client.get_bot_qrcode()
        assert result["qrcode"] == "abc123"
        mock_post.assert_called_once_with("get_bot_qrcode", {"bot_type": 3})

    @patch.object(ILinkBotsClient, "_post")
    def test_get_qrcode_status(self, mock_post):
        mock_post.return_value = {"status": "scanned"}
        result = ILinkBotsClient().get_qrcode_status("qr1")
        mock_post.assert_called_once_with("get_qrcode_status", {"qrcode": "qr1"}, quiet=True)
        assert result["status"] == "scanned"

    @patch.object(ILinkBotsClient, "_post")
    def test_getupdates(self, mock_post):
        mock_post.return_value = {"updates": [{"msg_id": "1"}]}
        result = ILinkBotsClient().getupdates(timeout=30)
        mock_post.assert_called_once_with("getupdates", {"timeout": 30}, timeout=40)
        assert len(result["updates"]) == 1

    @patch.object(ILinkBotsClient, "_post")
    def test_sendmessage(self, mock_post):
        mock_post.return_value = {"sent": True}
        client = ILinkBotsClient(bot_token="tok")
        result = client.sendmessage(context_token="ct1", content="hello")
        mock_post.assert_called_once_with(
            "sendmessage",
            {"context_token": "ct1", "content": "hello", "msg_type": "text"},
        )
        assert result["sent"] is True

    @patch.object(ILinkBotsClient, "_post")
    def test_getconfig(self, mock_post):
        mock_post.return_value = {"key": "val"}
        ILinkBotsClient().getconfig()
        mock_post.assert_called_once_with("getconfig")

    @patch.object(ILinkBotsClient, "_post")
    def test_sendtyping(self, mock_post):
        mock_post.return_value = {}
        ILinkBotsClient().sendtyping()
        mock_post.assert_called_once_with("sendtyping", {})

    @patch.object(ILinkBotsClient, "_post")
    def test_getuploadurl(self, mock_post):
        mock_post.return_value = {"url": "http://upload"}
        result = ILinkBotsClient().getuploadurl(file_type="image")
        mock_post.assert_called_once_with("getuploadurl", {"file_type": "image"})
        assert result["url"] == "http://upload"
