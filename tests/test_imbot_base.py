"""Unit tests for modules.channels.base data models."""

from modules.channels.base import BotConfig, ChannelMessage, ChannelStatus


class TestChannelMessage:
    def test_basic_creation(self):
        msg = ChannelMessage(
            message_id="m1", channel="wechat", sender_id="u1",
            sender_name="Alice", content="hello", msg_type="text",
        )
        assert msg.message_id == "m1"
        assert msg.channel == "wechat"
        assert msg.media_url is None
        assert msg.context_token is None

    def test_to_dict(self):
        msg = ChannelMessage(
            message_id="m2", channel="wechat", sender_id="u2",
            sender_name="Bob", content="hi", msg_type="text",
            context_token="ct123",
        )
        d = msg.to_dict()
        assert d["message_id"] == "m2"
        assert d["context_token"] == "ct123"
        assert "raw" not in d  # raw is excluded from to_dict

    def test_defaults(self):
        msg = ChannelMessage(
            message_id="m3", channel="tg", sender_id="u3",
            sender_name="", content="", msg_type="text",
        )
        assert msg.media_url is None
        assert msg.media_type is None
        assert msg.raw == {}


class TestBotConfig:
    def test_to_dict(self):
        cfg = BotConfig(
            bot_id="b1", channel_type="wechat",
            bot_token="tok", display_name="MyBot",
            enabled=True, config_data={"k": "v"},
        )
        d = cfg.to_dict()
        assert d["bot_id"] == "b1"
        assert d["config_data"] == {"k": "v"}
        assert d["enabled"] is True

    def test_defaults(self):
        cfg = BotConfig(bot_id="b2", channel_type="tg")
        assert cfg.bot_token is None
        assert cfg.enabled is True
        assert cfg.config_data == {}


class TestChannelStatus:
    def test_to_dict_spreads_extra(self):
        status = ChannelStatus(
            channel_type="wechat", is_running=True,
            is_authenticated=True, extra={"poll_timeout": 50},
        )
        d = status.to_dict()
        assert d["is_running"] is True
        assert d["poll_timeout"] == 50

    def test_error_field(self):
        status = ChannelStatus(
            channel_type="x", is_running=False,
            is_authenticated=False, error="something broke",
        )
        d = status.to_dict()
        assert d["error"] == "something broke"
