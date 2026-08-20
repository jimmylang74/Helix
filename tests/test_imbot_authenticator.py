"""Unit tests for imBots.wechat.authenticator WeChatAuthenticator."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Redirect DB before importing store
_tmp_db = os.path.join(tempfile.mkdtemp(), "test_auth.db")


@pytest.fixture(autouse=True, scope="session")
def _patch_db():
    import imBots.store as store_mod
    store_mod._db_path_cache = _tmp_db
    yield


@pytest.fixture(autouse=True)
def _clean_db():
    import imBots.store as store_mod
    conn = store_mod._init()
    try:
        conn.execute("DELETE FROM bot_sessions")
        conn.execute("DELETE FROM messages")
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def authenticator(mock_client):
    from imBots.wechat.authenticator import WeChatAuthenticator
    return WeChatAuthenticator(mock_client)


class TestStartAuth:
    def test_success(self, authenticator, mock_client):
        mock_client.get_bot_qrcode.return_value = {
            "qrcode": "qr_abc",
            "qrcode_img_content": "https://example.com/qr.png",
        }
        result = authenticator.start_auth()
        assert result["qrcode_id"] == "qr_abc"
        assert result["qrcode_img_content"] == "https://example.com/qr.png"
        assert result["qrcode_img_base64"].startswith("data:image/png;base64,")
        assert authenticator.is_authenticated is False

    def test_api_error(self, authenticator, mock_client):
        mock_client.get_bot_qrcode.side_effect = Exception("network down")
        result = authenticator.start_auth()
        assert "error" in result
        assert "network down" in result["error"]


class TestCheckAuthStatus:
    def test_no_qr_requested(self, authenticator):
        result = authenticator.check_auth_status()
        assert result["authenticated"] is False
        assert "No QR code" in result["error"]

    def test_not_yet_scanned(self, authenticator, mock_client):
        mock_client.get_bot_qrcode.return_value = {"qrcode": "qr1", "qrcode_img_content": ""}
        authenticator.start_auth()
        mock_client.get_qrcode_status.return_value = {"status": "waiting"}
        result = authenticator.check_auth_status()
        assert result["authenticated"] is False

    def test_confirmed_sets_auth(self, authenticator, mock_client):
        mock_client.get_bot_qrcode.return_value = {"qrcode": "qr1", "qrcode_img_content": ""}
        authenticator.start_auth()
        mock_client.get_qrcode_status.return_value = {
            "status": "confirmed",
            "bot_token": "new_bot_token",
        }
        mock_client.getconfig.return_value = {"cfg": True}
        result = authenticator.check_auth_status()
        assert result["authenticated"] is True
        assert result["bot_token"] == "new_bot_token"
        assert authenticator.is_authenticated is True
        mock_client.set_bot_token.assert_called_with("new_bot_token")

    def test_success_status_also_authenticates(self, authenticator, mock_client):
        mock_client.get_bot_qrcode.return_value = {"qrcode": "qr2", "qrcode_img_content": ""}
        authenticator.start_auth()
        mock_client.get_qrcode_status.return_value = {
            "status": "success",
            "bot_token": "tok_s",
        }
        mock_client.getconfig.return_value = {}
        result = authenticator.check_auth_status()
        assert result["authenticated"] is True

    def test_api_error(self, authenticator, mock_client):
        mock_client.get_bot_qrcode.return_value = {"qrcode": "qr3", "qrcode_img_content": ""}
        authenticator.start_auth()
        mock_client.get_qrcode_status.side_effect = Exception("timeout")
        result = authenticator.check_auth_status()
        assert result["authenticated"] is False
        assert "timeout" in result["error"]


class TestLogout:
    def test_logout_clears_state(self, authenticator, mock_client):
        mock_client.get_bot_qrcode.return_value = {"qrcode": "qr1", "qrcode_img_content": ""}
        authenticator.start_auth()
        mock_client.get_qrcode_status.return_value = {
            "status": "confirmed", "bot_token": "tok",
        }
        mock_client.getconfig.return_value = {}
        authenticator.check_auth_status()
        assert authenticator.is_authenticated is True

        result = authenticator.logout()
        assert result is True
        assert authenticator.is_authenticated is False
        mock_client.set_bot_token.assert_called_with("")


class TestRestoreFromStore:
    def test_restore_with_valid_session(self, authenticator, mock_client):
        from imBots.store import save_session
        save_session("wechat", bot_token="saved_tok", status="authenticated")
        result = authenticator.restore_from_store()
        assert result is True
        assert authenticator.is_authenticated is True
        mock_client.set_bot_token.assert_called_with("saved_tok")

    def test_restore_no_session(self, authenticator, mock_client):
        result = authenticator.restore_from_store()
        assert result is False
        assert authenticator.is_authenticated is False

    def test_restore_empty_token(self, authenticator, mock_client):
        from imBots.store import save_session
        save_session("wechat", bot_token="")
        result = authenticator.restore_from_store()
        assert result is False
