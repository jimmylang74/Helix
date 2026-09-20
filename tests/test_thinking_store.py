"""Unit tests for modules.channels.thinking.store (config + event results + RSS dedup)."""

import json
import os
import sqlite3
import tempfile

import pytest

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


# ── validate_config 字段校验 ────────────────────────────────────────────

class TestValidateConfig:
    def test_empty_fields_returns_defaults(self):
        from modules.channels.thinking.store import validate_config
        v = validate_config({})
        assert v["output_channels"] == []
        assert v["concurrency"] == 0
        assert v["rss"] == {"interval": 300, "feeds": []}
        assert v["webhook"] == {
            "enabled": True, "secret": "", "path": "/api/thinking/webhook",
        }

    def test_output_channels_none_and_empty_variants(self):
        from modules.channels.thinking.store import validate_config
        for raw in (None, "", [], [""], ["  "]):
            v = validate_config({"output_channels": raw})
            assert v["output_channels"] == []

    def test_output_channels_list_normalized(self):
        from modules.channels.thinking.store import validate_config
        v = validate_config({"output_channels": ["  iLinkBot ", "TELEGRAM", ""]})
        assert v["output_channels"] == ["ilinkbot", "telegram"]

    def test_output_channels_single_string(self):
        from modules.channels.thinking.store import validate_config
        v = validate_config({"output_channels": "ilinkbot"})
        assert v["output_channels"] == ["ilinkbot"]

    def test_output_channels_non_list_raises(self):
        from modules.channels.thinking.store import ThinkingValidationError, validate_config
        with pytest.raises(ThinkingValidationError):
            validate_config({"output_channels": 123})

    def test_concurrency_bounds_and_int_coercion(self):
        from modules.channels.thinking.store import validate_config
        assert validate_config({"concurrency": 0})["concurrency"] == 0
        assert validate_config({"concurrency": 100})["concurrency"] == 100
        assert validate_config({"concurrency": "5"})["concurrency"] == 5

    def test_concurrency_out_of_range_or_non_int_raises(self):
        from modules.channels.thinking.store import ThinkingValidationError, validate_config
        for bad in (-1, 101, "abc"):
            with pytest.raises(ThinkingValidationError):
                validate_config({"concurrency": bad})

    def test_rss_interval_bounds_and_coercion(self):
        from modules.channels.thinking.store import validate_config
        assert validate_config({"rss": {"interval": 30}})["rss"]["interval"] == 30
        assert validate_config({"rss": {"interval": 86400}})["rss"]["interval"] == 86400
        assert validate_config({"rss": {"interval": "300"}})["rss"]["interval"] == 300

    def test_rss_interval_out_of_range_raises(self):
        from modules.channels.thinking.store import ThinkingValidationError, validate_config
        for bad in (29, 86401, "x"):
            with pytest.raises(ThinkingValidationError):
                validate_config({"rss": {"interval": bad}})

    def test_rss_feeds_validation(self):
        from modules.channels.thinking.store import ThinkingValidationError, validate_config
        with pytest.raises(ThinkingValidationError):  # feeds 必须是数组
            validate_config({"rss": {"feeds": "x"}})
        with pytest.raises(ThinkingValidationError):  # 条目必须是对象
            validate_config({"rss": {"feeds": [1]}})
        with pytest.raises(ThinkingValidationError):  # url 必填
            validate_config({"rss": {"feeds": [{}]}})
        with pytest.raises(ThinkingValidationError):  # url 必须 http(s)://
            validate_config({"rss": {"feeds": [{"url": "ftp://x"}]}})

    def test_rss_feed_enabled_default_true(self):
        from modules.channels.thinking.store import validate_config
        feeds = validate_config(
            {"rss": {"feeds": [{"url": "https://example.com/feed.xml"}]}}
        )["rss"]["feeds"]
        assert feeds == [{"url": "https://example.com/feed.xml", "enabled": True}]

    def test_webhook_validation(self):
        from modules.channels.thinking.store import ThinkingValidationError, validate_config
        with pytest.raises(ThinkingValidationError):  # path 必须以 / 开头
            validate_config({"webhook": {"path": "api/x"}})
        v = validate_config(
            {"webhook": {"enabled": False, "secret": " s ", "path": "/p"}}
        )
        assert v["webhook"] == {"enabled": False, "secret": "s", "path": "/p"}


# ── 配置持久化（db/thinking.json） ──────────────────────────────────────

class TestConfigPersistence:
    def test_load_missing_file_returns_defaults(self):
        from modules.channels.thinking.store import load_config
        cfg = load_config()
        assert cfg["concurrency"] == 0
        assert cfg["rss"] == {"interval": 300, "feeds": []}
        assert cfg["webhook"] == {
            "enabled": True, "secret": "", "path": "/api/thinking/webhook",
        }

    def test_save_and_load_roundtrip(self):
        import modules.channels.thinking.store as store_mod
        cfg = {
            "output_channels": ["ilinkbot"],
            "concurrency": 3,
            "rss": {
                "interval": 600,
                "feeds": [{"url": "https://a.com/feed", "enabled": True}],
            },
            "webhook": {"enabled": True, "secret": "s3", "path": "/api/thinking/webhook"},
        }
        store_mod.save_config(cfg)
        loaded = store_mod.load_config()
        assert loaded["output_channels"] == ["ilinkbot"]
        assert loaded["concurrency"] == 3
        assert loaded["rss"]["interval"] == 600
        assert loaded["rss"]["feeds"] == [{"url": "https://a.com/feed", "enabled": True}]
        assert loaded["webhook"]["secret"] == "s3"

    def test_load_merges_missing_sections_with_defaults(self):
        import modules.channels.thinking.store as store_mod
        with open(store_mod._config_path(), "w", encoding="utf-8") as f:
            json.dump({"concurrency": 5}, f, ensure_ascii=False)
        loaded = store_mod.load_config()
        assert loaded["concurrency"] == 5
        assert loaded["rss"] == {"interval": 300, "feeds": []}
        assert loaded["webhook"]["enabled"] is True
        assert loaded["output_channels"] == []

    def test_load_clamps_out_of_range_values(self):
        import modules.channels.thinking.store as store_mod
        with open(store_mod._config_path(), "w", encoding="utf-8") as f:
            json.dump({"concurrency": 999, "rss": {"interval": 5}}, f, ensure_ascii=False)
        loaded = store_mod.load_config()
        assert loaded["concurrency"] == 100
        assert loaded["rss"]["interval"] == 30

    def test_load_corrupted_json_returns_defaults(self):
        import modules.channels.thinking.store as store_mod
        with open(store_mod._config_path(), "w", encoding="utf-8") as f:
            f.write("{oops")
        loaded = store_mod.load_config()
        assert loaded["concurrency"] == 0

    def test_load_non_dict_ignored(self):
        import modules.channels.thinking.store as store_mod
        with open(store_mod._config_path(), "w", encoding="utf-8") as f:
            json.dump([1, 2], f)
        assert store_mod.load_config()["concurrency"] == 0

    def test_ensure_config_creates_once_and_is_idempotent(self):
        import modules.channels.thinking.store as store_mod
        assert not os.path.exists(store_mod._config_path())
        store_mod.ensure_config()
        assert os.path.exists(store_mod._config_path())
        mtime = os.path.getmtime(store_mod._config_path())
        store_mod.ensure_config()  # 二次调用不覆盖
        assert os.path.getmtime(store_mod._config_path()) == mtime


# ── 事件结果（db/thinking.db · SQLite） ─────────────────────────────────

class TestEventStorage:
    @staticmethod
    def _save(event_id="evt_test1", event_type="webhook.push", status="success"):
        from modules.channels.thinking.store import save_event
        return save_event(
            event_id=event_id, event_type=event_type, title="WebHook 推送",
            content="hello", status=status,
            started_at="2026-09-20 10:00:00", finished_at="2026-09-20 10:00:05",
            duration_ms=5000, output="done",
            error="" if status == "success" else "boom",
        )

    def test_save_and_get_roundtrip(self):
        from modules.channels.thinking.store import get_events
        saved = self._save()
        rows = get_events()
        assert len(rows) == 1
        row = rows[0]
        assert row["event_id"] == saved["event_id"] == "evt_test1"
        assert row["event_type"] == "webhook.push"
        assert row["status"] == "success"
        assert row["output"] == "done"
        assert row["duration_ms"] == 5000

    def test_events_newest_first_with_limit(self):
        from modules.channels.thinking.store import get_events, save_event
        for i in range(3):
            save_event(
                event_id=f"evt_{i}", event_type="webhook.push", title=f"t{i}",
                content="", status="success",
                started_at=f"2026-09-20 10:0{i}:00",
                finished_at=f"2026-09-20 10:0{i}:00", duration_ms=0,
            )
        rows = get_events(limit=2)
        assert [r["event_id"] for r in rows] == ["evt_2", "evt_1"]

    def test_events_filter_by_type(self):
        from modules.channels.thinking.store import get_events, save_event
        save_event(
            event_id="evt_a", event_type="rss.feed", title="A", content="",
            status="success", started_at="2026-09-20 10:00:00",
            finished_at="2026-09-20 10:00:00", duration_ms=0,
        )
        save_event(
            event_id="evt_b", event_type="webhook.push", title="B", content="",
            status="success", started_at="2026-09-20 10:01:00",
            finished_at="2026-09-20 10:01:00", duration_ms=0,
        )
        rows = get_events(event_type="rss.feed")
        assert [r["event_id"] for r in rows] == ["evt_a"]

    def test_duplicate_event_id_raises_integrity_error(self):
        from modules.channels.thinking.store import save_event
        self._save(event_id="evt_dup")
        with pytest.raises(sqlite3.IntegrityError):
            self._save(event_id="evt_dup")

    def test_new_event_id_format(self):
        from modules.channels.thinking.store import new_event_id
        eid = new_event_id()
        assert eid.startswith("evt_")
        assert len(eid) == len("evt_") + 12


# ── RSS 去重（db/thinking.db · rss_seen 表） ────────────────────────────

class TestRssSeen:
    def test_is_entry_seen_then_marked(self):
        from modules.channels.thinking.store import is_entry_seen, mark_entries_seen
        assert is_entry_seen("https://a.com/feed", "e1") is False
        mark_entries_seen([("https://a.com/feed", "e1")])
        assert is_entry_seen("https://a.com/feed", "e1") is True
        # 不同源的同名条目互不影响
        assert is_entry_seen("https://b.com/feed", "e1") is False

    def test_mark_entries_seen_dedup(self):
        from modules.channels.thinking.store import is_entry_seen, mark_entries_seen
        mark_entries_seen([("u", "e"), ("u", "e"), ("u", "e2")])
        assert is_entry_seen("u", "e") is True
        assert is_entry_seen("u", "e2") is True

    def test_mark_entries_seen_empty_noop(self):
        from modules.channels.thinking.store import mark_entries_seen
        mark_entries_seen([])  # 不应抛异常

    def test_seen_cap_trims_oldest(self):
        from modules.channels.thinking.store import RSS_SEEN_CAP, is_entry_seen, mark_entries_seen
        entries = [(f"feed{i}", f"e{i}") for i in range(RSS_SEEN_CAP + 10)]
        mark_entries_seen(entries)
        assert is_entry_seen("feed0", "e0") is False          # 最旧被裁剪
        assert is_entry_seen("feed5", "e5") is False
        kept = is_entry_seen(f"feed{RSS_SEEN_CAP + 9}", f"e{RSS_SEEN_CAP + 9}")
        assert kept is True                                   # 最新保留