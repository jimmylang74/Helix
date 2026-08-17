"""
History Store — 请求历史持久化存储 (JSON 文件, 线程安全).

任务完成/失败/取消后由 Host 侧 (routes.py `_run`) 调用 record() 写入,
Web 控制台 "使用记录" 页面通过 history.get 读取.

会话集 (Session) 机制:
  所有 session_id 等于 _current_session_id 的记录构成当前活跃会话集,
  get_session_context() 返回同一会话集内所有请求的 user_request + final_answer,
  archive_current_session() 将当前会话集归档（写入唯一 ID）并开启新会话集.
"""

import json
import os
import threading
import uuid
from typing import Any, Dict, List, Optional

from modules.utils.logger import log_error

# 历史记录保留上限 (超出后丢弃最旧的)
MAX_HISTORY = 200

_lock = threading.Lock()
_history_path: Optional[str] = None

# ── 会话集管理 ─────────────────────────────────────────────

_current_session_id: str = "id_current_session"


def get_current_session_id() -> str:
    """返回当前活跃会话集 ID."""
    return _current_session_id


def archive_current_session() -> str:
    """归档当前会话集: 将所有当前会话集的记录写入唯一归档 ID, 并开启新会话集.

    Returns:
        被归档的旧 session_id.
    """
    global _current_session_id
    old_id = _current_session_id
    archive_id = f"archived_{uuid.uuid4().hex[:12]}"
    new_id = f"session_{uuid.uuid4().hex[:12]}"
    _current_session_id = new_id
    _update_session_ids(old_id, archive_id)
    return old_id


def _update_session_ids(old_id: str, new_id: str) -> None:
    """将磁盘上所有 old_id 的记录的 session_id 更新为 new_id (线程安全)."""
    try:
        with _lock:
            records = _load_locked()
            changed = False
            for rec in records:
                if rec.get("session_id") == old_id:
                    rec["session_id"] = new_id
                    changed = True
            if changed:
                _save_locked(records)
    except Exception as e:
        log_error(f"History store: failed to update session_ids: {e}")


def get_session_context() -> List[Dict[str, str]]:
    """返回当前会话集内所有记录的 user_request + final_answer 列表 (按时间正序).

    Returns:
        [{"user_request": "...", "final_answer": "..."}, ...]
    """
    try:
        with _lock:
            records = _load_locked()
            session_records = [
                r for r in records if r.get("session_id") == _current_session_id
            ]
            session_records.reverse()
            return [
                {
                    "user_request": r.get("user_request", ""),
                    "final_answer": r.get("final_answer", ""),
                }
                for r in session_records
            ]
    except Exception as e:
        log_error(f"History store: failed to get session context: {e}")
        return []


def _get_history_path() -> str:
    """历史文件路径: 项目根目录下的 db/history.json."""
    global _history_path
    if _history_path is None:
        _history_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "db", "history.json",
        )
    return _history_path


def record(entry: Dict[str, Any]) -> None:
    """追加一条历史记录 (线程安全). 最新记录在前保存."""
    try:
        with _lock:
            records = _load_locked()
            records.insert(0, entry)
            # 保留上限, 丢弃最旧的
            del records[MAX_HISTORY:]
            _save_locked(records)
    except Exception as e:
        log_error(f"History store: failed to record entry: {e}")


def get_history(limit: int = 100) -> List[Dict[str, Any]]:
    """读取历史记录, 按时间倒序 (最新在前)."""
    try:
        with _lock:
            records = _load_locked()
            if limit <= 0:
                return records
            return records[:limit]
    except Exception as e:
        log_error(f"History store: failed to read history: {e}")
        return []


def _load_locked() -> List[Dict[str, Any]]:
    """从磁盘加载全部记录 (调用方必须持有 _lock)."""
    path = _get_history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _save_locked(records: List[Dict[str, Any]]) -> None:
    """写入磁盘 (调用方必须持有 _lock). 确保 db/ 目录存在."""
    path = _get_history_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
