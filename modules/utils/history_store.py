"""
History Store — 请求历史持久化存储 (JSON 文件, 线程安全).

任务完成/失败/取消后由 Host 侧 (routes.py `_run`) 调用 record() 写入,
Web 控制台 "使用记录" 页面通过 history.get 读取.
"""

import json
import os
import threading
from typing import Any, Dict, List, Optional

from modules.utils.logger import log_error

# 历史记录保留上限 (超出后丢弃最旧的)
MAX_HISTORY = 200

_lock = threading.Lock()
_history_path: Optional[str] = None


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
