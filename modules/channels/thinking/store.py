"""
Thinking persistence — 思考通道配置与事件处理结果存储。

- 通道配置: db/thinking.json（JSON 对象，人工可读可编辑；RSS / WebHook /
  输出通道 / 并发设置；文件缺失时按默认值运行）
- 事件结果: db/thinking.db（SQLite 单表，INSERT 追加 + 按需查询，
  天然无条数上限）
- RSS 去重: db/thinking.db 内 rss_seen 表（feed_url + entry_id 唯一键，
  服务重启后不会重复处理已见条目；总量封顶自动裁剪）

并发约定：配置文件用 RLock + 原子写（临时文件 + os.replace）；事件库用
独立锁，每次操作短连接（与 modules/channels/cron/store.py 同风格）。
"""

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from modules.utils.paths import project_path
from modules.utils.logger import log_error, log_warning

MIN_RSS_INTERVAL = 30
MAX_RSS_INTERVAL = 86400
MAX_CONCURRENCY = 100
RSS_SEEN_CAP = 2000
WEBHOOK_DEFAULT_PATH = "/api/thinking/webhook"

_HTTP_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

DEFAULT_CONFIG: Dict[str, Any] = {
    "output_channels": [],
    "concurrency": 0,
    "rss": {
        "interval": 300,
        "feeds": [],
    },
    "webhook": {
        "enabled": True,
        "secret": "",
        "path": WEBHOOK_DEFAULT_PATH,
    },
}

_config_lock = threading.RLock()
_config_path_cache: Optional[str] = None

_events_lock = threading.Lock()
_events_db_path_cache: Optional[str] = None


class ThinkingValidationError(ValueError):
    """思考通道配置校验失败。"""


def _config_path() -> str:
    global _config_path_cache
    if _config_path_cache is None:
        _config_path_cache = project_path("db", "thinking.json")
    return _config_path_cache


def _events_db_path() -> str:
    global _events_db_path_cache
    if _events_db_path_cache is None:
        path = project_path("db", "thinking.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _events_db_path_cache = path
    return _events_db_path_cache


# ═══════════════════════════════════════════════════════════════
# 配置（db/thinking.json）
# ═══════════════════════════════════════════════════════════════


def load_config() -> Dict[str, Any]:
    """读取通道配置；文件缺失/损坏时返回默认配置（不落盘，由 ensure_config 负责初始化）。"""
    with _config_lock:
        try:
            with open(_config_path(), "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return _normalize_config(dict(DEFAULT_CONFIG))
        except (json.JSONDecodeError, OSError) as e:
            log_error(f"thinking store: 读取 thinking.json 失败: {e}")
            return _normalize_config(dict(DEFAULT_CONFIG))
        if not isinstance(raw, dict):
            log_error("thinking store: thinking.json 顶层必须是对象，已忽略")
            return _normalize_config(dict(DEFAULT_CONFIG))
        merged = {
            **DEFAULT_CONFIG,
            **raw,
            "rss": {**DEFAULT_CONFIG["rss"], **raw.get("rss", {})},
            "webhook": {**DEFAULT_CONFIG["webhook"], **raw.get("webhook", {})},
        }
        return _normalize_config(merged)


def save_config(config: Dict[str, Any]) -> None:
    """原子写回配置（临时文件 + os.replace，避免半截文件）。"""
    with _config_lock:
        path = _config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)


def ensure_config() -> None:
    """幂等初始化：文件缺失时写入默认配置；存在则跳过（不覆盖用户数据）。"""
    with _config_lock:
        path = _config_path()
        if os.path.exists(path):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_config(dict(DEFAULT_CONFIG))


def validate_config(fields: Dict[str, Any]) -> Dict[str, Any]:
    """完整校验并规范化配置，返回仅含合法键的规范化字典；非法即抛 ThinkingValidationError。"""
    normalized: Dict[str, Any] = {}

    raw_output = fields.get("output_channels")
    if raw_output in (None, "", [], [""]):
        normalized["output_channels"] = []
    else:
        if isinstance(raw_output, str):
            raw_output = [raw_output]
        if not isinstance(raw_output, list):
            raise ThinkingValidationError("output_channels 必须是数组")
        channels = []
        for c in raw_output:
            s = str(c).strip().lower()
            if s:
                channels.append(s)
        normalized["output_channels"] = channels

    raw_conc = fields.get("concurrency", 0)
    try:
        conc = int(raw_conc)
    except (TypeError, ValueError):
        raise ThinkingValidationError(
            f"concurrency 必须为 0-{MAX_CONCURRENCY} 的整数（0=不限制）"
        )
    if not 0 <= conc <= MAX_CONCURRENCY:
        raise ThinkingValidationError(
            f"concurrency 必须为 0-{MAX_CONCURRENCY} 的整数（0=不限制）"
        )
    normalized["concurrency"] = conc

    raw_rss = fields.get("rss") or {}
    if not isinstance(raw_rss, dict):
        raise ThinkingValidationError("rss 必须是对象")
    try:
        interval = int(raw_rss.get("interval", DEFAULT_CONFIG["rss"]["interval"]))
    except (TypeError, ValueError):
        raise ThinkingValidationError(
            f"rss.interval 必须为 {MIN_RSS_INTERVAL}-{MAX_RSS_INTERVAL} 的整数（秒）"
        )
    if not MIN_RSS_INTERVAL <= interval <= MAX_RSS_INTERVAL:
        raise ThinkingValidationError(
            f"rss.interval 必须为 {MIN_RSS_INTERVAL}-{MAX_RSS_INTERVAL} 的整数（秒）"
        )
    raw_feeds = raw_rss.get("feeds") or []
    if not isinstance(raw_feeds, list):
        raise ThinkingValidationError("rss.feeds 必须是数组")
    feeds = []
    for item in raw_feeds:
        if not isinstance(item, dict):
            raise ThinkingValidationError("rss.feeds 的每个条目必须是对象")
        url = str(item.get("url", "")).strip()
        if not url:
            raise ThinkingValidationError("rss.feeds 存在缺少 url 的条目")
        if not _HTTP_URL_RE.match(url):
            raise ThinkingValidationError(f"订阅地址必须以 http(s):// 开头: {url}")
        feeds.append({"url": url, "enabled": bool(item.get("enabled", True))})
    normalized["rss"] = {"interval": interval, "feeds": feeds}

    raw_wh = fields.get("webhook") or {}
    if not isinstance(raw_wh, dict):
        raise ThinkingValidationError("webhook 必须是对象")
    enabled = bool(raw_wh.get("enabled", True))
    secret = str(raw_wh.get("secret") or "").strip()
    path = str(raw_wh.get("path") or WEBHOOK_DEFAULT_PATH).strip()
    if not path.startswith("/"):
        raise ThinkingValidationError("webhook.path 必须以 / 开头")
    normalized["webhook"] = {"enabled": enabled, "secret": secret, "path": path}

    return normalized


def _normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """加载时宽容规范化（越界值钳制、非法条目丢弃并告警），绝不抛异常。"""
    normalized: Dict[str, Any] = {}

    raw_output = raw.get("output_channels")
    if isinstance(raw_output, (list, str)):
        channels = []
        for c in (raw_output if isinstance(raw_output, list) else [raw_output]):
            s = str(c).strip().lower()
            if s:
                channels.append(s)
        normalized["output_channels"] = channels
    else:
        normalized["output_channels"] = []

    try:
        conc = int(raw.get("concurrency", 0))
    except (TypeError, ValueError):
        conc = 0
    normalized["concurrency"] = max(0, min(conc, MAX_CONCURRENCY))

    raw_rss = raw.get("rss") or {}
    try:
        interval = int(raw_rss.get("interval", DEFAULT_CONFIG["rss"]["interval"]))
    except (TypeError, ValueError):
        interval = DEFAULT_CONFIG["rss"]["interval"]
    interval = max(MIN_RSS_INTERVAL, min(interval, MAX_RSS_INTERVAL))
    feeds = []
    for item in raw_rss.get("feeds") or []:
        if not isinstance(item, dict):
            log_warning(f"thinking store: 跳过非法订阅源条目: {item}")
            continue
        url = str(item.get("url", "")).strip()
        if not url or not _HTTP_URL_RE.match(url):
            log_warning(f"thinking store: 跳过非法订阅源地址: {url!r}")
            continue
        feeds.append({"url": url, "enabled": bool(item.get("enabled", True))})
    normalized["rss"] = {"interval": interval, "feeds": feeds}

    raw_wh = raw.get("webhook") or {}
    path = str(raw_wh.get("path") or WEBHOOK_DEFAULT_PATH).strip()
    if not path.startswith("/"):
        path = WEBHOOK_DEFAULT_PATH
    normalized["webhook"] = {
        "enabled": bool(raw_wh.get("enabled", True)),
        "secret": str(raw_wh.get("secret") or "").strip(),
        "path": path,
    }

    return normalized


# ═══════════════════════════════════════════════════════════════
# 事件结果（db/thinking.db · SQLite）
# ═══════════════════════════════════════════════════════════════


def _connect_events() -> sqlite3.Connection:
    conn = sqlite3.connect(_events_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_events_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS thinking_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    TEXT UNIQUE,
            event_type  TEXT NOT NULL,
            title       TEXT DEFAULT '',
            content     TEXT DEFAULT '',
            status      TEXT DEFAULT 'success',
            started_at  TEXT DEFAULT '',
            finished_at TEXT DEFAULT '',
            duration_ms INTEGER DEFAULT 0,
            output      TEXT DEFAULT '',
            error       TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_thinking_events_type
            ON thinking_events(event_type, id DESC);
    """)


def save_event(
    event_id: str,
    event_type: str,
    title: str,
    content: str,
    status: str,
    started_at: str,
    finished_at: str,
    duration_ms: int = 0,
    output: str = "",
    error: str = "",
) -> Dict[str, Any]:
    """追加一条事件处理记录并返回该记录。"""
    with _events_lock:
        conn = _connect_events()
        try:
            _ensure_events_table(conn)
            conn.execute(
                """INSERT INTO thinking_events
                   (event_id, event_type, title, content, status,
                    started_at, finished_at, duration_ms, output, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id, event_type, title, content, status,
                    started_at, finished_at, duration_ms,
                    output, error,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "event_id": event_id, "event_type": event_type, "title": title,
        "content": content, "status": status,
        "started_at": started_at, "finished_at": finished_at,
        "duration_ms": duration_ms, "output": output, "error": error,
    }


def get_events(limit: int = 100, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """按处理开始时间倒序返回最近事件处理记录。"""
    limit = max(1, min(int(limit or 100), 1000))
    event_type = (event_type or "").strip() or None
    with _events_lock:
        conn = _connect_events()
        try:
            _ensure_events_table(conn)
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM thinking_events WHERE event_type = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM thinking_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [_row_to_event(r) for r in rows]
        finally:
            conn.close()


def _row_to_event(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "title": row["title"],
        "content": row["content"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row["duration_ms"],
        "output": row["output"],
        "error": row["error"],
    }


# ═══════════════════════════════════════════════════════════════
# RSS 去重（db/thinking.db · rss_seen 表）
# ═══════════════════════════════════════════════════════════════


def _ensure_seen_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rss_seen (
            feed_url TEXT NOT NULL,
            entry_id TEXT NOT NULL,
            PRIMARY KEY (feed_url, entry_id)
        );
    """)


def is_entry_seen(feed_url: str, entry_id: str) -> bool:
    """该订阅源的条目是否已处理过。"""
    with _events_lock:
        conn = _connect_events()
        try:
            _ensure_seen_table(conn)
            row = conn.execute(
                "SELECT 1 FROM rss_seen WHERE feed_url = ? AND entry_id = ?",
                (feed_url, entry_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()


def mark_entries_seen(entries: List[Tuple[str, str]]) -> None:
    """批量标记已见条目（insert-or-ignore），并裁剪超出容量上限的旧记录。"""
    if not entries:
        return
    with _events_lock:
        conn = _connect_events()
        try:
            _ensure_seen_table(conn)
            conn.executemany(
                "INSERT OR IGNORE INTO rss_seen (feed_url, entry_id) VALUES (?, ?)",
                entries,
            )
            count = conn.execute("SELECT COUNT(*) FROM rss_seen").fetchone()[0]
            if count > RSS_SEEN_CAP:
                conn.execute(
                    "DELETE FROM rss_seen WHERE rowid NOT IN "
                    "(SELECT rowid FROM rss_seen ORDER BY rowid DESC LIMIT ?)",
                    (RSS_SEEN_CAP,),
                )
            conn.commit()
        finally:
            conn.close()


# ── Helpers ────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_event_id() -> str:
    """生成事件处理记录 ID（与内部处理请求共用，事件维度去重）。"""
    return f"evt_{uuid.uuid4().hex[:12]}"


__all__ = [
    "ThinkingValidationError",
    "MIN_RSS_INTERVAL",
    "MAX_RSS_INTERVAL",
    "MAX_CONCURRENCY",
    "WEBHOOK_DEFAULT_PATH",
    "DEFAULT_CONFIG",
    "load_config",
    "save_config",
    "ensure_config",
    "validate_config",
    "save_event",
    "get_events",
    "is_entry_seen",
    "mark_entries_seen",
    "new_event_id",
]