"""Unit tests for thinking event sources (RSS parse/poll + webhook handle_request)."""

import json
import os
import tempfile
from unittest import mock

import pytest

import modules.channels.thinking.store as thinking_store
from modules.channels.thinking.sources import (
    RssEventSource,
    WebhookEventSource,
    parse_feed,
)
from modules.events import EventBus
from modules.events.base import RssEvent, WebhookEvent

# Redirect storage paths to a temp dir before touching the store
_tmpdir = tempfile.mkdtemp()


@pytest.fixture(autouse=True, scope="session")
def _patch_paths():
    import modules.channels.thinking.store as store_mod
    store_mod._config_path_cache = os.path.join(_tmpdir, "thinking.json")
    store_mod._events_db_path_cache = os.path.join(_tmpdir, "thinking.db")
    yield
    store_mod._config_path_cache = None
    store_mod._events_db_path_cache = None


@pytest.fixture(autouse=True)
def _clean_storage():
    import modules.channels.thinking.store as store_mod
    db_path = store_mod._events_db_path()
    for path in (
        store_mod._config_path(),
        db_path,
        db_path + "-wal",
        db_path + "-shm",
    ):
        if os.path.exists(path):
            os.remove(path)
    yield


class _FakeBus(EventBus):
    """捕获发布事件；无消费线程、无全局状态。"""

    def __init__(self):
        super().__init__()
        self.published = []

    def publish(self, event):
        self.published.append(event)


_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Demo Feed</title>
<item>
  <guid>https://example.com/post/1</guid>
  <title>First Post</title>
  <link>https://example.com/post/1</link>
  <description>Summary one</description>
</item>
<item>
  <title>No Guid</title>
  <link>https://example.com/post/2</link>
  <description>Summary two</description>
</item>
<item>
  <title>No Link</title>
  <guid>guid-3</guid>
  <description>Summary three</description>
</item>
</channel></rss>"""

_ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Demo</title>
<entry>
  <id>urn:uuid:1</id>
  <title>Atom Post</title>
  <link rel="alternate" href="https://example.com/atom/1"/>
  <summary>Atom summary</summary>
</entry>
<entry>
  <id>urn:uuid:2</id>
  <title>Self Link Only</title>
  <link rel="self" href="https://example.com/feed"/>
  <content>Atom content fallback</content>
</entry>
</feed>"""


# ── parse_feed（RSS 2.0 / Atom 纯函数） ─────────────────────────────────

class TestParseFeed:
    def test_rss_guid_priority(self):
        entries = parse_feed(_RSS_XML)
        assert len(entries) == 3
        assert entries[0] == {
            "id": "https://example.com/post/1",
            "title": "First Post",
            "link": "https://example.com/post/1",
            "summary": "Summary one",
        }

    def test_rss_guid_fallback_to_link(self):
        entries = parse_feed(_RSS_XML)
        assert entries[1]["id"] == "https://example.com/post/2"  # 无 guid → link
        assert entries[2]["id"] == "guid-3"                      # 有 guid 无 link 保留

    def test_rss_item_without_any_id_skipped(self):
        xml = b"""<rss version="2.0"><channel>
        <item><title>no id at all</title></item>
        </channel></rss>"""
        assert parse_feed(xml) == []

    def test_atom_entries(self):
        entries = parse_feed(_ATOM_XML)
        assert len(entries) == 2
        assert entries[0] == {
            "id": "urn:uuid:1",
            "title": "Atom Post",
            "link": "https://example.com/atom/1",
            "summary": "Atom summary",
        }

    def test_atom_self_link_only_keeps_entry_with_empty_link(self):
        second = parse_feed(_ATOM_XML)[1]
        assert second["id"] == "urn:uuid:2"
        assert second["link"] == ""                                  # rel=self 不取
        assert second["summary"] == "Atom content fallback"          # 无 summary 回退 content

    def test_invalid_xml_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_feed(b"<rss><channel>")

    def test_unsupported_root_raises(self):
        with pytest.raises(ValueError):
            parse_feed(b"<html><body>hi</body></html>")


# ── RssEventSource（轮询型） ───────────────────────────────────────────

class TestRssEventSource:
    def _make_source(self, feeds):
        bus = _FakeBus()
        thinking_store.save_config({"rss": {"interval": 300, "feeds": feeds}})
        return RssEventSource(event_bus=bus), bus

    def test_poll_once_publishes_new_entries_and_dedups(self):
        src, bus = self._make_source(
            [{"url": "https://a.com/feed.xml", "enabled": True}]
        )
        with mock.patch(
            "modules.channels.thinking.sources.fetch_feed", return_value=_RSS_XML
        ) as fetch:
            assert src.poll_once() == 3
            fetch.assert_called_once_with("https://a.com/feed.xml")
            assert len(bus.published) == 3
            assert all(isinstance(e, RssEvent) for e in bus.published)
            first = bus.published[0]
            assert first.event_type == "rss.feed"
            assert first.get("feed_url") == "https://a.com/feed.xml"
            assert first.get("entry_id") == "https://example.com/post/1"
            assert first.get("entry_title") == "First Post"
            # 二次轮询全部已见 → 不再发布
            assert src.poll_once() == 0
            assert len(bus.published) == 3

    def test_disabled_feed_not_fetched(self):
        src, bus = self._make_source(
            [
                {"url": "https://off.com/feed.xml", "enabled": False},
                {"url": "https://on.com/feed.xml", "enabled": True},
            ]
        )
        with mock.patch(
            "modules.channels.thinking.sources.fetch_feed", return_value=_RSS_XML
        ) as fetch:
            assert src.poll_once() == 3
            assert [c.args[0] for c in fetch.call_args_list] == [
                "https://on.com/feed.xml"
            ]

    def test_single_feed_failure_does_not_break_others(self):
        src, bus = self._make_source(
            [
                {"url": "https://bad.com/feed.xml", "enabled": True},
                {"url": "https://good.com/feed.xml", "enabled": True},
            ]
        )

        def _fetch(url):
            if "bad" in url:
                raise RuntimeError("network down")
            return _RSS_XML

        with mock.patch(
            "modules.channels.thinking.sources.fetch_feed", side_effect=_fetch
        ), mock.patch("modules.channels.thinking.sources.log_warning"):
            assert src.poll_once() == 3  # 仅好源产出，坏源仅告警
            assert len(bus.published) == 3


# ── WebhookEventSource（事件驱动型） ───────────────────────────────────

class TestWebhookEventSource:
    def _make_source(self, webhook_cfg):
        bus = _FakeBus()
        thinking_store.save_config({"webhook": webhook_cfg})
        return WebhookEventSource(event_bus=bus), bus

    def test_disabled_endpoint_returns_404(self):
        src, bus = self._make_source({"enabled": False, "secret": ""})
        result = src.handle_request({"content": "hi"}, {})
        assert result["ok"] is False
        assert result["status"] == 404
        assert bus.published == []

    def test_secret_mismatch_returns_401(self):
        src, bus = self._make_source({"enabled": True, "secret": "s3cret"})
        result = src.handle_request({"content": "hi"}, {"X-Webhook-Secret": "wrong"})
        assert result["ok"] is False
        assert result["status"] == 401
        assert bus.published == []

    def test_secret_match_publishes(self):
        src, bus = self._make_source({"enabled": True, "secret": "s3cret"})
        result = src.handle_request(
            {"content": "hello world"}, {"X-Webhook-Secret": "s3cret"}
        )
        assert result["ok"] is True
        assert result["status"] == 200
        assert result["event_id"]
        assert len(bus.published) == 1
        ev = bus.published[0]
        assert isinstance(ev, WebhookEvent)
        assert ev.event_type == "webhook.push"
        assert ev.get("content") == "hello world"

    def test_content_defaults_to_serialized_payload(self):
        src, bus = self._make_source({"enabled": True, "secret": ""})
        result = src.handle_request({"a": 1, "b": [2]}, {})
        assert result["ok"] is True
        ev = bus.published[0]
        assert ev.get("content") == json.dumps({"a": 1, "b": [2]}, ensure_ascii=False)
        assert ev.get("raw") == {"a": 1, "b": [2]}

    def test_origin_from_payload_source_or_user_agent(self):
        src, bus = self._make_source({"enabled": True, "secret": ""})
        src.handle_request({"source": "jenkins", "content": "x"}, {})
        assert bus.published[0].get("origin") == "jenkins"
        src.handle_request({"content": "y"}, {"User-Agent": "curl/8.0"})
        assert bus.published[1].get("origin") == "curl/8.0"