"""
Cron persistence — 任务定义与运行结果存储。

- 任务定义: db/cron.json（JSON 数组，人工可读可编辑；用户直接改文件时，
  调度器经 mtime 变化感知并热重载）
- 运行结果: db/cron.db（SQLite 单表，INSERT 追加 + 按需查询，
  天然无条数上限）

并发约定：任务文件用 RLock + 原子写（临时文件 + os.replace）；结果库用
独立锁，每次操作短连接（与 modules/channels/store.py 同风格）。
"""

import json
import os
import re
import sqlite3
import threading
import uuid
from calendar import monthrange
from datetime import datetime
from typing import Any, Dict, List, Optional

from modules.utils.paths import project_path
from modules.utils.logger import log_error, log_info, log_warning

VALID_REPEATS = ("daily", "weekly", "monthly")
VALID_TASK_TYPES = ("system", "agent")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_tasks_lock = threading.RLock()
_tasks_path_cache: Optional[str] = None

_results_lock = threading.Lock()
_results_db_path_cache: Optional[str] = None


class CronValidationError(ValueError):
    """定时任务字段校验失败。"""


def _tasks_path() -> str:
    global _tasks_path_cache
    if _tasks_path_cache is None:
        _tasks_path_cache = project_path("db", "cron.json")
    return _tasks_path_cache


def _results_db_path() -> str:
    global _results_db_path_cache
    if _results_db_path_cache is None:
        path = project_path("db", "cron.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _results_db_path_cache = path
    return _results_db_path_cache


# ═══════════════════════════════════════════════════════════════
# 字段校验与规范化
# ═══════════════════════════════════════════════════════════════


def validate_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """完整校验并规范化一个任务的全部字段，返回仅含合法键的规范化字典。

    必填：title / time / repeat / task_type / description；
    条件必填：weekly 需 weekday（0-6），monthly 需 day_of_month（1-31）。
    """
    normalized: Dict[str, Any] = {}

    title = str(fields.get("title", "")).strip()
    if not title:
        raise CronValidationError("title 不能为空")
    normalized["title"] = title

    time_str = str(fields.get("time", "")).strip()
    if not _TIME_RE.match(time_str):
        raise CronValidationError("time 格式必须为 HH:MM（24 小时制），如 09:30")
    normalized["time"] = time_str

    repeat = str(fields.get("repeat", "")).strip().lower()
    if repeat not in VALID_REPEATS:
        raise CronValidationError(
            f"repeat 必须为 {'/'.join(VALID_REPEATS)} 之一"
        )
    normalized["repeat"] = repeat

    raw_weekday = fields.get("weekday")
    if raw_weekday not in (None, ""):
        try:
            wd = int(raw_weekday)
        except (TypeError, ValueError):
            raise CronValidationError("weekday 必须为 0-6 的整数（0=周一）")
        if not 0 <= wd <= 6:
            raise CronValidationError("weekday 必须为 0-6 的整数（0=周一）")
        normalized["weekday"] = wd
    else:
        normalized["weekday"] = None

    raw_dom = fields.get("day_of_month")
    if raw_dom not in (None, ""):
        try:
            dom = int(raw_dom)
        except (TypeError, ValueError):
            raise CronValidationError("day_of_month 必须为 1-31 的整数")
        if not 1 <= dom <= 31:
            raise CronValidationError("day_of_month 必须为 1-31 的整数")
        normalized["day_of_month"] = dom
    else:
        normalized["day_of_month"] = None

    if repeat == "weekly" and normalized["weekday"] is None:
        raise CronValidationError(
            "repeat=weekly 时必须提供 weekday（0=周一…6=周日）"
        )
    if repeat == "monthly" and normalized["day_of_month"] is None:
        raise CronValidationError("repeat=monthly 时必须提供 day_of_month（1-31）")

    task_type = str(fields.get("task_type", "")).strip().lower()
    if task_type not in VALID_TASK_TYPES:
        raise CronValidationError(
            f"task_type 必须为 {'/'.join(VALID_TASK_TYPES)} 之一"
        )
    normalized["task_type"] = task_type

    desc = str(fields.get("description", "")).strip()
    if not desc:
        raise CronValidationError("description 不能为空（system=命令行，agent=任务描述）")
    normalized["description"] = desc

    normalized["enabled"] = bool(fields.get("enabled", True))

    raw_output = fields.get("output_channels")
    if raw_output in (None, "", [], [""]):
        normalized["output_channels"] = []
    else:
        if isinstance(raw_output, str):
            raw_output = [raw_output]
        channels = []
        for c in raw_output:
            s = str(c).strip().lower()
            if s:
                channels.append(s)
        normalized["output_channels"] = channels

    return normalized


def _normalize_task(raw: Any) -> Optional[Dict[str, Any]]:
    """加载时对单条任务做宽容规范化（补默认值）；结构损坏则丢弃并告警。"""
    if not isinstance(raw, dict):
        return None
    try:
        task = validate_fields(raw)
    except CronValidationError as e:
        log_warning(f"cron store: 跳过非法任务条目 ({e}): {raw}")
        return None
    task["id"] = str(raw.get("id") or f"cron_{uuid.uuid4().hex[:8]}")
    task["created_at"] = str(raw.get("created_at") or _now())
    task["updated_at"] = str(raw.get("updated_at") or task["created_at"])
    return task


# ═══════════════════════════════════════════════════════════════
# 任务定义 CRUD（db/cron.json）
# ═══════════════════════════════════════════════════════════════


def tasks_mtime() -> float:
    """cron.json 的修改时间（文件不存在返回 0.0）。调度器据此感知外部改动。"""
    try:
        return os.path.getmtime(_tasks_path())
    except OSError:
        return 0.0


def load_tasks() -> List[Dict[str, Any]]:
    """读取全部任务（文件缺失视为空列表）。"""
    with _tasks_lock:
        try:
            with open(_tasks_path(), "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as e:
            log_error(f"cron store: 读取 cron.json 失败: {e}")
            return []
        if not isinstance(raw, list):
            log_error("cron store: cron.json 顶层必须是数组，已忽略")
            return []
        tasks = []
        for item in raw:
            task = _normalize_task(item)
            if task is not None:
                tasks.append(task)
        return tasks


def save_tasks(tasks: List[Dict[str, Any]]) -> None:
    """原子写回任务数组（临时文件 + os.replace，避免半截文件）。"""
    with _tasks_lock:
        path = _tasks_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    for task in load_tasks():
        if task["id"] == task_id:
            return task
    return None


def ensure_schema() -> None:
    """幂等迁移：为缺少 output_channels 字段的任务补空数组。

    必须直接检查文件原始内容——load_tasks 经 validate_fields 规范化时会
    无条件补充 output_channels 默认值，缺字段状态被掩盖；故此处绕过
    规范化层读原始 JSON，仅当存在缺字段条目时才补写（其余字段与值原样
    保留，原子写回）；已全部具备时不做任何写操作（mtime 不变）。
    """
    with _tasks_lock:
        path = _tasks_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, OSError) as e:
            log_error(f"cron store: ensure_schema 读取 cron.json 失败: {e}")
            return
        if not isinstance(raw, list):
            return
        if not any(isinstance(item, dict) and "output_channels" not in item for item in raw):
            return
        for item in raw:
            if isinstance(item, dict) and "output_channels" not in item:
                item["output_channels"] = []
        save_tasks(raw)
        log_info("cron store: migrated cron.json — added output_channels=[]")


def create_task(fields: Dict[str, Any]) -> Dict[str, Any]:
    """新建任务：生成 ID 与时间戳，写入文件并返回完整任务。"""
    normalized = validate_fields(fields)
    task = {
        "id": f"cron_{uuid.uuid4().hex[:8]}",
        **normalized,
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _tasks_lock:
        tasks = load_tasks()
        tasks.append(task)
        save_tasks(tasks)
    log_info(f"cron store: created task {task['id']} '{task['title']}'")
    return task


def update_task(task_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """按 ID 部分更新任务，返回更新后的完整任务；不存在抛 KeyError。

    做法：把传入字段合并进当前任务后做整体校验（weekly/monthly 完整性
    自然满足检查），再写回；repeat 切走时清掉不再使用的字段。
    """
    patch = {k: v for k, v in (fields or {}).items() if k != "id"}
    with _tasks_lock:
        tasks = load_tasks()
        for i, task in enumerate(tasks):
            if task["id"] != task_id:
                continue
            merged = {**task, **patch}
            validated = validate_fields(merged)
            if validated["repeat"] != "weekly":
                validated["weekday"] = None
            if validated["repeat"] != "monthly":
                validated["day_of_month"] = None
            updated = {
                **task,
                **validated,
                "id": task["id"],
                "created_at": task.get("created_at", ""),
                "updated_at": _now(),
            }
            tasks[i] = updated
            save_tasks(tasks)
            log_info(f"cron store: updated task {task_id}")
            return updated
    raise KeyError(f"定时任务不存在: {task_id}")


def delete_task(task_id: str) -> bool:
    """按 ID 删除任务，返回是否存在。"""
    with _tasks_lock:
        tasks = load_tasks()
        remaining = [t for t in tasks if t["id"] != task_id]
        if len(remaining) == len(tasks):
            return False
        save_tasks(remaining)
        log_info(f"cron store: deleted task {task_id}")
        return True


# ═══════════════════════════════════════════════════════════════
# 运行结果（db/cron.db · SQLite）
# ═══════════════════════════════════════════════════════════════


def _connect_results() -> sqlite3.Connection:
    conn = sqlite3.connect(_results_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_results_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cron_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id   TEXT UNIQUE,
            cron_id     TEXT NOT NULL,
            title       TEXT DEFAULT '',
            task_type   TEXT DEFAULT '',
            status      TEXT DEFAULT 'success',
            started_at  TEXT DEFAULT '',
            finished_at TEXT DEFAULT '',
            duration_ms INTEGER DEFAULT 0,
            output      TEXT DEFAULT '',
            error       TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_cron_results_cron
            ON cron_results(cron_id, started_at DESC);
    """)


def save_result(
    cron_id: str,
    title: str,
    task_type: str,
    status: str,
    started_at: str,
    finished_at: str,
    duration_ms: int = 0,
    output: str = "",
    error: str = "",
) -> Dict[str, Any]:
    """追加一条运行结果并返回该记录。"""
    result_id = f"res_{uuid.uuid4().hex[:12]}"
    with _results_lock:
        conn = _connect_results()
        try:
            _ensure_results_table(conn)
            conn.execute(
                """INSERT INTO cron_results
                   (result_id, cron_id, title, task_type, status,
                    started_at, finished_at, duration_ms, output, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result_id, cron_id, title, task_type, status,
                    started_at, finished_at, duration_ms,
                    output, error,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "result_id": result_id, "cron_id": cron_id, "title": title,
        "task_type": task_type, "status": status,
        "started_at": started_at, "finished_at": finished_at,
        "duration_ms": duration_ms, "output": output, "error": error,
    }


def get_results(limit: int = 100, cron_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """按开始时间倒序返回最近运行结果。"""
    limit = max(1, min(int(limit or 100), 1000))
    with _results_lock:
        conn = _connect_results()
        try:
            _ensure_results_table(conn)
            if cron_id:
                rows = conn.execute(
                    "SELECT * FROM cron_results WHERE cron_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (cron_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM cron_results ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [_row_to_result(r) for r in rows]
        finally:
            conn.close()


def get_last_result_map() -> Dict[str, Dict[str, Any]]:
    """返回每个任务最近一次结果的映射（cron_id → result），供列表展示。"""
    with _results_lock:
        conn = _connect_results()
        try:
            _ensure_results_table(conn)
            rows = conn.execute(
                """SELECT * FROM cron_results
                   WHERE id IN (SELECT MAX(id) FROM cron_results GROUP BY cron_id)"""
            ).fetchall()
            return {r["cron_id"]: _row_to_result(r) for r in rows}
        finally:
            conn.close()


def _row_to_result(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "result_id": row["result_id"],
        "cron_id": row["cron_id"],
        "title": row["title"],
        "task_type": row["task_type"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row["duration_ms"],
        "output": row["output"],
        "error": row["error"],
    }


# ── Helpers ────────────────────────────────────────────────────────────────


def next_occurrence(task: Dict[str, Any], after: datetime) -> Optional[datetime]:
    """计算任务在 ``after`` 之后的下一次触发时刻（本地时间，严格晚于 after）。

    - daily:  每天 HH:MM
    - weekly: 每周指定 weekday(0=周一…6=周日) HH:MM
    - monthly: 每月指定日（超出当月天数时取当月最后一天）HH:MM
    """
    hh, mm = (int(x) for x in task["time"].split(":"))
    repeat = task["repeat"]

    def _at(base_date) -> datetime:
        return base_date.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if repeat == "daily":
        candidate = _at(after)
        if candidate <= after:
            candidate = _at(after.fromordinal(after.toordinal() + 1))
        return candidate

    if repeat == "weekly":
        target = int(task["weekday"])
        candidate = _at(after)
        days_ahead = (target - candidate.weekday()) % 7
        candidate = candidate.fromordinal(candidate.toordinal() + days_ahead)
        candidate = _at(candidate)
        if candidate <= after:
            candidate = _at(candidate.fromordinal(candidate.toordinal() + 7))
        return candidate

    if repeat == "monthly":
        dom = int(task["day_of_month"])

        def _month_candidate(year: int, month: int) -> datetime:
            day = min(dom, monthrange(year, month)[1])
            return after.replace(
                year=year, month=month, day=day,
                hour=hh, minute=mm, second=0, microsecond=0,
            )

        candidate = _month_candidate(after.year, after.month)
        if candidate <= after:
            year, month = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
            candidate = _month_candidate(year, month)
        return candidate

    return None


def describe_schedule(task: Dict[str, Any]) -> str:
    """人类可读的重复描述，如 '每天 09:30' / '每周三 09:30' / '每月15日 09:30'。"""
    weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    if task["repeat"] == "daily":
        return f"每天 {task['time']}"
    if task["repeat"] == "weekly":
        wd = int(task.get("weekday") or 0)
        return f"每{weekdays[wd]} {task['time']}"
    if task["repeat"] == "monthly":
        return f"每月{task.get('day_of_month')}日 {task['time']}"
    return task["time"]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "CronValidationError",
    "VALID_REPEATS",
    "VALID_TASK_TYPES",
    "load_tasks",
    "save_tasks",
    "get_task",
    "create_task",
    "update_task",
    "delete_task",
    "ensure_schema",
    "validate_fields",
    "tasks_mtime",
    "save_result",
    "get_results",
    "get_last_result_map",
    "next_occurrence",
    "describe_schedule",
]
