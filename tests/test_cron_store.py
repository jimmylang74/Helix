"""Unit tests for modules.channels.cron.store (JSON tasks + SQLite results)."""

import json
import os
import tempfile
from datetime import datetime

import pytest

# Redirect storage paths to a temp dir before touching the store
_tmpdir = tempfile.mkdtemp()


@pytest.fixture(autouse=True, scope="session")
def _patch_paths():
    import modules.channels.cron.store as store_mod
    store_mod._tasks_path_cache = os.path.join(_tmpdir, "cron.json")
    store_mod._results_db_path_cache = os.path.join(_tmpdir, "cron.db")
    yield
    store_mod._tasks_path_cache = None
    store_mod._results_db_path_cache = None


@pytest.fixture(autouse=True)
def _clean_storage():
    import modules.channels.cron.store as store_mod
    db_path = store_mod._results_db_path()
    for path in (
        store_mod._tasks_path(),
        db_path,
        db_path + "-wal",
        db_path + "-shm",
    ):
        if os.path.exists(path):
            os.remove(path)
    yield


_VALID_DAILY = {
    "title": "每日备份",
    "time": "09:30",
    "repeat": "daily",
    "task_type": "system",
    "description": "echo backup",
}


# ── Field validation ──────────────────────────────────────────────────────

class TestValidateFields:
    def test_valid_daily_defaults(self):
        from modules.channels.cron.store import validate_fields
        f = validate_fields(dict(_VALID_DAILY))
        assert f["title"] == "每日备份"
        assert f["time"] == "09:30"
        assert f["repeat"] == "daily"
        assert f["task_type"] == "system"
        assert f["weekday"] is None
        assert f["day_of_month"] is None
        assert f["enabled"] is True

    def test_empty_title_raises(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        bad = dict(_VALID_DAILY, title="  ")
        with pytest.raises(CronValidationError):
            validate_fields(bad)

    def test_bad_time_format_raises(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        for t in ("9:30", "24:00", "09:60", "abc"):
            with pytest.raises(CronValidationError):
                validate_fields(dict(_VALID_DAILY, time=t))

    def test_bad_repeat_raises(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        with pytest.raises(CronValidationError):
            validate_fields(dict(_VALID_DAILY, repeat="hourly"))

    def test_bad_task_type_raises(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        with pytest.raises(CronValidationError):
            validate_fields(dict(_VALID_DAILY, task_type="shell"))

    def test_empty_description_raises(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        with pytest.raises(CronValidationError):
            validate_fields(dict(_VALID_DAILY, description=""))

    def test_weekly_requires_weekday(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        weekly = dict(_VALID_DAILY, repeat="weekly")
        with pytest.raises(CronValidationError):
            validate_fields(weekly)
        f = validate_fields(dict(weekly, weekday="2"))
        assert f["weekday"] == 2

    def test_weekday_out_of_range_raises(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        with pytest.raises(CronValidationError):
            validate_fields(dict(_VALID_DAILY, repeat="weekly", weekday=7))
        with pytest.raises(CronValidationError):
            validate_fields(dict(_VALID_DAILY, repeat="weekly", weekday="x"))

    def test_monthly_requires_day_of_month(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        monthly = dict(_VALID_DAILY, repeat="monthly")
        with pytest.raises(CronValidationError):
            validate_fields(monthly)
        f = validate_fields(dict(monthly, day_of_month="31"))
        assert f["day_of_month"] == 31

    def test_day_of_month_out_of_range_raises(self):
        from modules.channels.cron.store import CronValidationError, validate_fields
        with pytest.raises(CronValidationError):
            validate_fields(dict(_VALID_DAILY, repeat="monthly", day_of_month=32))

    # ── output_channels 规范化 ────────────────────────────────────────

    def test_output_channels_default_empty(self):
        from modules.channels.cron.store import validate_fields
        f = validate_fields(dict(_VALID_DAILY))
        assert f["output_channels"] == []

    def test_output_channels_none_and_empty_variants(self):
        from modules.channels.cron.store import validate_fields
        for raw in (None, "", [], [""], ["  "]):
            f = validate_fields(dict(_VALID_DAILY, output_channels=raw))
            assert f["output_channels"] == []

    def test_output_channels_list_normalized(self):
        from modules.channels.cron.store import validate_fields
        f = validate_fields(
            dict(_VALID_DAILY, output_channels=["  iLinkBot ", "TELEGRAM", ""])
        )
        assert f["output_channels"] == ["ilinkbot", "telegram"]

    def test_output_channels_single_string(self):
        from modules.channels.cron.store import validate_fields
        f = validate_fields(dict(_VALID_DAILY, output_channels="ilinkbot"))
        assert f["output_channels"] == ["ilinkbot"]


# ── Task CRUD (db/cron.json) ─────────────────────────────────────────────

class TestTaskCrud:
    def test_create_and_load_roundtrip(self):
        from modules.channels.cron.store import create_task, load_tasks
        task = create_task(dict(_VALID_DAILY))
        assert task["id"].startswith("cron_")
        assert task["created_at"] and task["updated_at"]
        loaded = load_tasks()
        assert len(loaded) == 1
        assert loaded[0]["id"] == task["id"]
        assert loaded[0]["title"] == "每日备份"

    def test_get_task(self):
        from modules.channels.cron.store import create_task, get_task
        task = create_task(dict(_VALID_DAILY))
        fetched = get_task(task["id"])
        assert fetched is not None
        assert fetched["title"] == "每日备份"
        assert get_task("nope") is None

    def test_tasks_mtime_missing_file_is_zero(self):
        from modules.channels.cron.store import tasks_mtime
        assert tasks_mtime() == 0.0

    def test_tasks_mtime_after_create(self):
        from modules.channels.cron.store import create_task, tasks_mtime
        create_task(dict(_VALID_DAILY))
        assert tasks_mtime() > 0.0

    def test_update_partial_merge_keeps_id(self):
        from modules.channels.cron.store import create_task, get_task, update_task
        task = create_task(dict(_VALID_DAILY))
        updated = update_task(task["id"], {"time": "22:00"})
        assert updated["time"] == "22:00"
        assert updated["title"] == "每日备份"
        fetched = get_task(task["id"])
        assert fetched is not None
        assert fetched["time"] == "22:00"
        assert fetched["created_at"] == task["created_at"]

    def test_update_clears_stale_fields_on_repeat_switch(self):
        from modules.channels.cron.store import create_task, update_task
        task = create_task(dict(_VALID_DAILY))
        switched = update_task(
            task["id"], {"repeat": "weekly", "weekday": 3}
        )
        assert switched["repeat"] == "weekly"
        assert switched["weekday"] == 3
        back = update_task(task["id"], {"repeat": "daily"})
        assert back["weekday"] is None

    def test_update_missing_id_raises_keyerror(self):
        from modules.channels.cron.store import update_task
        with pytest.raises(KeyError):
            update_task("ghost", {"time": "01:00"})

    def test_delete_returns_true_then_false(self):
        from modules.channels.cron.store import create_task, delete_task
        task = create_task(dict(_VALID_DAILY))
        assert delete_task(task["id"]) is True
        assert delete_task(task["id"]) is False

    def test_load_skips_invalid_entries(self):
        import modules.channels.cron.store as store_mod
        good = {
            "id": "cron_ok1", "title": "好任务", "time": "08:00",
            "repeat": "daily", "task_type": "system", "description": "ok",
        }
        raw = [good, {"id": "cron_bad", "title": "", "time": "99:99"}, "not-a-dict"]
        with open(store_mod._tasks_path(), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        tasks = store_mod.load_tasks()
        assert [t["id"] for t in tasks] == ["cron_ok1"]

    def test_load_non_array_ignored(self):
        import modules.channels.cron.store as store_mod
        with open(store_mod._tasks_path(), "w", encoding="utf-8") as f:
            json.dump({"oops": True}, f)
        assert store_mod.load_tasks() == []

    def test_output_channels_roundtrip(self):
        from modules.channels.cron.store import create_task, load_tasks
        task = create_task(dict(_VALID_DAILY, output_channels=["ilinkbot"]))
        loaded = load_tasks()
        assert loaded[0]["output_channels"] == ["ilinkbot"]

    def test_update_output_channels_clears_with_empty_list(self):
        from modules.channels.cron.store import create_task, update_task
        task = create_task(dict(_VALID_DAILY, output_channels=["ilinkbot"]))
        cleared = update_task(task["id"], {"output_channels": []})
        assert cleared["output_channels"] == []

    def test_load_handles_missing_output_channels(self):
        import modules.channels.cron.store as store_mod
        legacy = {
            "id": "cron_legacy", "title": "老任务", "time": "08:00",
            "repeat": "daily", "task_type": "system", "description": "ok",
        }
        with open(store_mod._tasks_path(), "w", encoding="utf-8") as f:
            json.dump([legacy], f, ensure_ascii=False)
        tasks = store_mod.load_tasks()
        assert tasks[0]["output_channels"] == []
        assert tasks[0]["title"] == "老任务"


# ── ensure_schema 迁移 (output_channels) ────────────────────────────────

class TestEnsureSchema:
    def _write_raw(self, raw):
        import modules.channels.cron.store as store_mod
        with open(store_mod._tasks_path(), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)

    def _read_raw(self):
        import modules.channels.cron.store as store_mod
        with open(store_mod._tasks_path(), "r", encoding="utf-8") as f:
            return json.load(f)

    def test_migrates_legacy_task_preserving_fields(self):
        from modules.channels.cron.store import ensure_schema
        legacy = {
            "id": "cron_l1", "title": "旧任务", "time": "07:30",
            "repeat": "weekly", "weekday": 1, "task_type": "system",
            "description": "df -h", "enabled": False,
            "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-02 00:00:00",
        }
        self._write_raw([legacy])
        ensure_schema()
        migrated = self._read_raw()[0]
        assert migrated["output_channels"] == []
        for k, v in legacy.items():
            assert migrated[k] == v

    def test_partially_migrated_only_patches_missing(self):
        from modules.channels.cron.store import ensure_schema
        self._write_raw([
            {"id": "cron_a", "title": "A", "time": "08:00", "repeat": "daily",
             "task_type": "system", "description": "a"},
            {"id": "cron_b", "title": "B", "time": "09:00", "repeat": "daily",
             "task_type": "system", "description": "b", "output_channels": ["ilinkbot"]},
        ])
        ensure_schema()
        raw = self._read_raw()
        assert raw[0]["output_channels"] == []
        assert raw[1]["output_channels"] == ["ilinkbot"]

    def test_no_write_when_already_migrated(self):
        import os
        from modules.channels.cron.store import ensure_schema
        self._write_raw([
            {"id": "cron_a", "title": "A", "time": "08:00", "repeat": "daily",
             "task_type": "system", "description": "a", "output_channels": []},
        ])
        mtime_before = os.path.getmtime(
            __import__("modules.channels.cron.store", fromlist=["_tasks_path"])._tasks_path()
        )
        ensure_schema()
        assert os.path.getmtime(
            __import__("modules.channels.cron.store", fromlist=["_tasks_path"])._tasks_path()
        ) == mtime_before

    def test_empty_file_and_non_array_are_noops(self):
        import os
        from modules.channels.cron.store import ensure_schema
        store_mod = __import__("modules.channels.cron.store", fromlist=["_tasks_path"])
        path = store_mod._tasks_path()
        for payload in ("[]", '{"oops": 1}'):
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload)
            mtime_before = os.path.getmtime(path)
            ensure_schema()
            assert os.path.getmtime(path) == mtime_before


# ── Run results (db/cron.db · SQLite) ────────────────────────────────────

class TestResults:
    @staticmethod
    def _save(cron_id, title, status="success", started="2026-08-24 09:00:00"):
        from modules.channels.cron.store import save_result
        return save_result(
            cron_id=cron_id, title=title, task_type="system",
            status=status, started_at=started,
            finished_at=started, duration_ms=120,
            output="out", error="err" if status != "success" else "",
        )

    def test_save_and_get_roundtrip(self):
        from modules.channels.cron.store import get_results
        saved = self._save("cron_a", "任务A")
        rows = get_results()
        assert len(rows) == 1
        row = rows[0]
        assert row["result_id"] == saved["result_id"]
        assert row["cron_id"] == "cron_a"
        assert row["duration_ms"] == 120
        assert row["output"] == "out"

    def test_results_newest_first_with_limit(self):
        from modules.channels.cron.store import get_results
        self._save("cron_a", "t1", started="2026-08-24 09:00:00")
        self._save("cron_a", "t2", started="2026-08-24 10:00:00")
        self._save("cron_a", "t3", started="2026-08-24 11:00:00")
        rows = get_results(limit=2)
        assert [r["started_at"] for r in rows] == [
            "2026-08-24 11:00:00", "2026-08-24 10:00:00",
        ]

    def test_results_filter_by_cron_id(self):
        from modules.channels.cron.store import get_results
        self._save("cron_a", "A")
        self._save("cron_b", "B")
        rows = get_results(cron_id="cron_b")
        assert len(rows) == 1
        assert rows[0]["cron_id"] == "cron_b"

    def test_last_result_map(self):
        from modules.channels.cron.store import get_last_result_map
        self._save("cron_a", "A-old", started="2026-08-24 08:00:00")
        self._save("cron_a", "A-new", started="2026-08-24 09:00:00")
        self._save("cron_b", "B", started="2026-08-24 07:00:00")
        mapping = get_last_result_map()
        assert set(mapping) == {"cron_a", "cron_b"}
        assert mapping["cron_a"]["title"] == "A-new"


# ── Schedule computation ─────────────────────────────────────────────────

class TestSchedule:
    def test_daily_future_same_day(self):
        from modules.channels.cron.store import next_occurrence
        task = {"time": "09:30", "repeat": "daily"}
        nxt = next_occurrence(task, datetime(2026, 8, 24, 8, 0))
        assert nxt == datetime(2026, 8, 24, 9, 30)

    def test_daily_past_moves_to_tomorrow(self):
        from modules.channels.cron.store import next_occurrence
        task = {"time": "09:30", "repeat": "daily"}
        nxt = next_occurrence(task, datetime(2026, 8, 24, 23, 0))
        assert nxt == datetime(2026, 8, 25, 9, 30)

    def test_weekly_picks_target_weekday(self):
        from modules.channels.cron.store import next_occurrence
        task = {"time": "08:00", "repeat": "weekly", "weekday": 2}
        nxt = next_occurrence(task, datetime(2026, 8, 24, 10, 0))  # Monday
        assert nxt is not None
        assert nxt.weekday() == 2
        assert nxt == datetime(2026, 8, 26, 8, 0)

    def test_weekly_wraps_to_next_week(self):
        from modules.channels.cron.store import next_occurrence
        task = {"time": "08:00", "repeat": "weekly", "weekday": 6}
        nxt = next_occurrence(task, datetime(2026, 8, 24, 10, 0))  # Monday
        assert nxt == datetime(2026, 8, 30, 8, 0)

    def test_monthly_clamps_to_last_day(self):
        from modules.channels.cron.store import next_occurrence
        task = {"time": "09:30", "repeat": "monthly", "day_of_month": 31}
        nxt = next_occurrence(task, datetime(2026, 4, 10, 12, 0))  # April has 30 days
        assert nxt is not None
        assert (nxt.year, nxt.month, nxt.day) == (2026, 4, 30)

    def test_describe_schedule(self):
        from modules.channels.cron.store import describe_schedule
        assert describe_schedule({"time": "09:30", "repeat": "daily"}) == "每天 09:30"
        assert describe_schedule(
            {"time": "09:30", "repeat": "weekly", "weekday": 2}
        ) == "每周三 09:30"
        assert describe_schedule(
            {"time": "09:30", "repeat": "monthly", "day_of_month": 15}
        ) == "每月15日 09:30"
