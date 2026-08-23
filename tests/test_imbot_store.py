"""Unit tests for modules.channels.store SQLite persistence."""

import json
import os
import tempfile

import pytest

# Redirect DB to a temp file before importing store
_tmpdir = tempfile.mkdtemp()
_tmp_db = os.path.join(_tmpdir, "test_imbot.db")


@pytest.fixture(autouse=True, scope="session")
def _patch_db_path():
    import modules.channels.store as store_mod
    store_mod._db_path_cache = _tmp_db
    yield
    store_mod._db_path_cache = None


@pytest.fixture(autouse=True)
def _clean_tables():
    import modules.channels.store as store_mod
    conn = store_mod._init()
    try:
        conn.execute("DELETE FROM bot_sessions")
        conn.execute("DELETE FROM messages")
        conn.commit()
    finally:
        conn.close()
    yield


# ── Session tests ───────────────────────────────────────────────────────

class TestBotSessions:
    def test_save_and_get(self):
        from modules.channels.store import save_session, get_session
        save_session("wechat", bot_token="tok1", display_name="Bot", status="auth")
        s = get_session("wechat")
        assert s is not None
        assert s["bot_token"] == "tok1"
        assert s["display_name"] == "Bot"
        assert s["status"] == "auth"
        assert s["enabled"] is True

    def test_get_nonexistent(self):
        from modules.channels.store import get_session
        assert get_session("telegram") is None

    def test_upsert_updates(self):
        from modules.channels.store import save_session, get_session
        save_session("wechat", bot_token="v1")
        save_session("wechat", bot_token="v2", status="running")
        s = get_session("wechat")
        assert s["bot_token"] == "v2"
        assert s["status"] == "running"

    def test_get_all_sessions(self):
        from modules.channels.store import save_session, get_all_sessions
        save_session("wechat", bot_token="t1")
        save_session("telegram", bot_token="t2")
        all_s = get_all_sessions()
        types = {s["channel_type"] for s in all_s}
        assert "wechat" in types
        assert "telegram" in types

    def test_update_status(self):
        from modules.channels.store import save_session, update_session_status, get_session
        save_session("wechat", bot_token="t")
        update_session_status("wechat", "connected")
        assert get_session("wechat")["status"] == "connected"

    def test_update_field_bot_token(self):
        from modules.channels.store import save_session, update_session_field, get_session
        save_session("wechat", bot_token="old")
        update_session_field("wechat", "bot_token", "new")
        assert get_session("wechat")["bot_token"] == "new"

    def test_update_field_config_data(self):
        from modules.channels.store import save_session, update_session_field, get_session
        save_session("wechat")
        update_session_field("wechat", "config_data", {"key": "val"})
        s = get_session("wechat")
        assert s["config_data"] == {"key": "val"}

    def test_update_field_enabled(self):
        from modules.channels.store import save_session, update_session_field, get_session
        save_session("wechat", enabled=True)
        update_session_field("wechat", "enabled", False)
        assert get_session("wechat")["enabled"] is False

    def test_delete_session(self):
        from modules.channels.store import save_session, delete_session, get_session
        save_session("wechat")
        delete_session("wechat")
        assert get_session("wechat") is None


# ── Message tests ───────────────────────────────────────────────────────

class TestMessages:
    def test_save_and_get(self):
        from modules.channels.store import save_message, get_messages
        save_message(
            channel="wechat", direction="incoming", message_id="msg1",
            sender_id="u1", content="hello", msg_type="text",
        )
        msgs = get_messages("wechat")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hello"
        assert msgs[0]["direction"] == "incoming"

    def test_duplicate_message_id_ignored(self):
        from modules.channels.store import save_message, get_messages
        save_message(channel="wechat", direction="incoming", message_id="dup1", content="a")
        save_message(channel="wechat", direction="incoming", message_id="dup1", content="b")
        msgs = get_messages("wechat")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "a"

    def test_limit_parameter(self):
        from modules.channels.store import save_message, get_messages
        for i in range(10):
            save_message(channel="wechat", direction="incoming", message_id=f"m{i}", content=str(i))
        msgs = get_messages("wechat", limit=3)
        assert len(msgs) == 3
        # newest first
        assert msgs[0]["content"] == "9"

    def test_get_context_token(self):
        from modules.channels.store import save_message, get_context_token
        save_message(channel="wechat", direction="outgoing", message_id="o1", content="hi")
        assert get_context_token("wechat") is None
        save_message(
            channel="wechat", direction="incoming", message_id="i1",
            content="hey", context_token="ctx_token_abc",
        )
        assert get_context_token("wechat") == "ctx_token_abc"

    def test_prune_old_messages(self):
        from modules.channels import store as store_mod
        from modules.channels.store import save_message, get_messages
        orig = store_mod._MAX_MESSAGES
        store_mod._MAX_MESSAGES = 20
        try:
            for i in range(30):
                save_message(channel="prune", direction="incoming", message_id=f"p{i}", content=str(i))
            msgs = get_messages("prune", limit=50)
            assert len(msgs) == 20
            assert msgs[0]["content"] == "29"
            assert msgs[-1]["content"] == "10"
        finally:
            store_mod._MAX_MESSAGES = orig

    def test_different_channels_isolated(self):
        from modules.channels.store import save_message, get_messages
        save_message(channel="ch_a", direction="incoming", message_id="a1", content="from A")
        save_message(channel="ch_b", direction="incoming", message_id="b1", content="from B")
        assert len(get_messages("ch_a")) == 1
        assert len(get_messages("ch_b")) == 1
