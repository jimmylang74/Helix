"""
SQLite persistence for iBot sessions and messages.

Tables:
  bot_sessions — per-channel authentication tokens & config
  messages     — conversation messages (newest first query)
"""

import json
import os
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from modules.utils.paths import project_path

_db_path_cache: Optional[str] = None
_lock = threading.Lock()

_MAX_MESSAGES = 1000  # keep at most N rows; older ones pruned on insert

# 进行中会话的固定标识；clear_context 归档时替换为一次性 UUID
ACTIVE_SESSION_ID = "active"


def _db_path() -> str:
    global _db_path_cache
    if _db_path_cache is None:
        _db_path_cache = project_path("db", "imbot.db")
    return _db_path_cache


def _connect() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bot_sessions (
            channel_type  TEXT PRIMARY KEY,
            bot_token     TEXT,
            display_name  TEXT DEFAULT '',
            enabled       INTEGER DEFAULT 1,
            config_data   TEXT DEFAULT '{}',
            status        TEXT DEFAULT 'disconnected',
            last_active   TEXT,
            updated_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id    TEXT UNIQUE,
            channel       TEXT NOT NULL,
            direction     TEXT NOT NULL,
            sender_id     TEXT DEFAULT '',
            sender_name   TEXT DEFAULT '',
            content       TEXT DEFAULT '',
            msg_type      TEXT DEFAULT 'text',
            media_url     TEXT,
            media_type    TEXT,
            context_token TEXT,
            raw_data      TEXT DEFAULT '{}',
            timestamp     TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_messages_channel
            ON messages(channel, timestamp DESC);

        CREATE TABLE IF NOT EXISTS agent_sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel       TEXT NOT NULL,
            session_id    TEXT DEFAULT 'active',
            user_request  TEXT DEFAULT '',
            final_answer  TEXT DEFAULT '',
            created_at    TEXT DEFAULT (datetime('now')),
            archived_at   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_agent_sessions_channel
            ON agent_sessions(channel, session_id);
    """)


def _init() -> sqlite3.Connection:
    conn = _connect()
    _ensure_tables(conn)
    return conn


# ── Bot Sessions ───────────────────────────────────────────────────────────


def save_session(
    channel_type: str,
    bot_token: str = "",
    display_name: str = "",
    enabled: bool = True,
    config_data: Optional[Dict[str, Any]] = None,
    status: str = "disconnected",
) -> None:
    with _lock:
        conn = _init()
        try:
            conn.execute(
                """INSERT INTO bot_sessions
                   (channel_type, bot_token, display_name, enabled, config_data, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(channel_type) DO UPDATE SET
                       bot_token     = excluded.bot_token,
                       display_name  = excluded.display_name,
                       enabled       = excluded.enabled,
                       config_data   = excluded.config_data,
                       status        = excluded.status,
                       updated_at    = datetime('now')
                """,
                (
                    channel_type,
                    bot_token,
                    display_name,
                    1 if enabled else 0,
                    json.dumps(config_data or {}, ensure_ascii=False),
                    status,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def get_session(channel_type: str) -> Optional[Dict[str, Any]]:
    with _lock:
        conn = _init()
        try:
            row = conn.execute(
                "SELECT * FROM bot_sessions WHERE channel_type = ?",
                (channel_type,),
            ).fetchone()
            if row is None:
                return None
            return _row_to_session(row)
        finally:
            conn.close()


def get_all_sessions() -> List[Dict[str, Any]]:
    with _lock:
        conn = _init()
        try:
            rows = conn.execute(
                "SELECT * FROM bot_sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [_row_to_session(r) for r in rows]
        finally:
            conn.close()


def update_session_status(channel_type: str, status: str) -> None:
    with _lock:
        conn = _init()
        try:
            conn.execute(
                "UPDATE bot_sessions SET status = ?, updated_at = datetime('now') "
                "WHERE channel_type = ?",
                (status, channel_type),
            )
            conn.commit()
        finally:
            conn.close()


def update_session_field(channel_type: str, field: str, value: Any) -> None:
    """Update a single field on a session row."""
    allowed = {"bot_token", "display_name", "enabled", "config_data", "status", "last_active"}
    if field not in allowed:
        return
    if field == "config_data" and isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    if field == "enabled":
        value = 1 if value else 0
    with _lock:
        conn = _init()
        try:
            conn.execute(
                f"UPDATE bot_sessions SET {field} = ?, updated_at = datetime('now') "
                "WHERE channel_type = ?",
                (value, channel_type),
            )
            conn.commit()
        finally:
            conn.close()


def delete_session(channel_type: str) -> None:
    with _lock:
        conn = _init()
        try:
            conn.execute("DELETE FROM bot_sessions WHERE channel_type = ?", (channel_type,))
            conn.commit()
        finally:
            conn.close()


def _row_to_session(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "channel_type": row["channel_type"],
        "bot_token": row["bot_token"] or "",
        "display_name": row["display_name"] or "",
        "enabled": bool(row["enabled"]),
        "config_data": json.loads(row["config_data"] or "{}"),
        "status": row["status"] or "disconnected",
        "last_active": row["last_active"] or "",
    }


# ── Messages ───────────────────────────────────────────────────────────────


def save_message(
    channel: str,
    direction: str,
    message_id: str,
    sender_id: str = "",
    sender_name: str = "",
    content: str = "",
    msg_type: str = "text",
    media_url: Optional[str] = None,
    media_type: Optional[str] = None,
    context_token: Optional[str] = None,
    raw_data: Optional[Dict[str, Any]] = None,
    timestamp: Optional[str] = None,
) -> None:
    with _lock:
        conn = _init()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO messages
                   (message_id, channel, direction, sender_id, sender_name,
                    content, msg_type, media_url, media_type, context_token,
                    raw_data, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    channel,
                    direction,
                    sender_id,
                    sender_name,
                    content,
                    msg_type,
                    media_url,
                    media_type,
                    context_token,
                    json.dumps(raw_data or {}, ensure_ascii=False),
                    timestamp or _now(),
                ),
            )
            conn.commit()
            _prune_old(conn, channel)
        finally:
            conn.close()


def get_messages(
    channel: str, limit: int = 50
) -> List[Dict[str, Any]]:
    with _lock:
        conn = _init()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE channel = ? "
                "ORDER BY id DESC LIMIT ?",
                (channel, limit),
            ).fetchall()
            return [_row_to_message(r) for r in rows]
        finally:
            conn.close()


# ── Agent Sessions（每通道会话上下文）───────────────────────────────────────


def save_agent_context(channel: str, user_request: str, final_answer: str) -> None:
    """Append one request/answer exchange to the channel's active session."""
    with _lock:
        conn = _init()
        try:
            conn.execute(
                """INSERT INTO agent_sessions
                   (channel, session_id, user_request, final_answer)
                   VALUES (?, ?, ?, ?)""",
                (channel, ACTIVE_SESSION_ID, user_request, final_answer),
            )
            conn.commit()
        finally:
            conn.close()


def get_active_agent_context(channel: str) -> List[Dict[str, str]]:
    """Return all exchanges of the channel's ongoing session (oldest first)."""
    with _lock:
        conn = _init()
        try:
            rows = conn.execute(
                "SELECT user_request, final_answer FROM agent_sessions "
                "WHERE channel = ? AND session_id = ? ORDER BY id ASC",
                (channel, ACTIVE_SESSION_ID),
            ).fetchall()
            return [
                {
                    "user_request": r["user_request"] or "",
                    "final_answer": r["final_answer"] or "",
                }
                for r in rows
            ]
        finally:
            conn.close()


def archive_agent_session(channel: str) -> tuple[str, int]:
    """Start a new session: stamp the active batch with a one-off UUID.

    Messages/rows are never deleted — the old session stays fully stored
    under its archived id.  Returns ``(archived_session_id, row_count)``.
    """
    archived_id = f"ses_{uuid.uuid4().hex[:12]}"
    with _lock:
        conn = _init()
        try:
            cursor = conn.execute(
                "UPDATE agent_sessions "
                "SET session_id = ?, archived_at = datetime('now') "
                "WHERE channel = ? AND session_id = ?",
                (archived_id, channel, ACTIVE_SESSION_ID),
            )
            conn.commit()
            return archived_id, cursor.rowcount
        finally:
            conn.close()


def get_context_token(channel: str) -> Optional[str]:
    """Return the context_token from the most recent incoming message."""
    with _lock:
        conn = _init()
        try:
            row = conn.execute(
                "SELECT context_token FROM messages "
                "WHERE channel = ? AND direction = 'incoming' AND context_token IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (channel,),
            ).fetchone()
            return row["context_token"] if row else None
        finally:
            conn.close()


def get_to_user_id(channel: str) -> Optional[str]:
    """Return the sender_id from the most recent incoming message.

    Used to resolve ``to_user_id`` for outgoing messages when no in-memory
    state is available (e.g. after a server restart).
    """
    with _lock:
        conn = _init()
        try:
            row = conn.execute(
                "SELECT sender_id FROM messages "
                "WHERE channel = ? AND direction = 'incoming' AND sender_id != '' "
                "ORDER BY id DESC LIMIT 1",
                (channel,),
            ).fetchone()
            return row["sender_id"] if row else None
        finally:
            conn.close()


def _prune_old(conn: sqlite3.Connection, channel: str) -> None:
    """Delete oldest messages beyond the retention limit."""
    conn.execute(
        """DELETE FROM messages WHERE channel = ? AND id NOT IN
           (SELECT id FROM messages WHERE channel = ?
            ORDER BY id DESC LIMIT ?)""",
        (channel, channel, _MAX_MESSAGES),
    )
    conn.commit()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_message(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "channel": row["channel"],
        "direction": row["direction"],
        "sender_id": row["sender_id"] or "",
        "sender_name": row["sender_name"] or "",
        "content": row["content"] or "",
        "msg_type": row["msg_type"] or "text",
        "media_url": row["media_url"],
        "media_type": row["media_type"],
        "context_token": row["context_token"],
        "raw_data": json.loads(row["raw_data"] or "{}"),
        "timestamp": row["timestamp"] or "",
    }
