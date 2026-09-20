"""
Helix Profile — Thinking 内省智能体的画像与固定注入上下文（host 侧）。

Thinking 规划提示词需要三份固定上下文：Helix.md 智能体画像、当前日期时间、
默认地点。HelixCore 保持零 host 依赖，因此本模块负责把它们**预渲染为纯文本
字符串**，组装成 process_request(context_injections=...) 的字典，由编排器
透传到 build_thinking_planning_system_prompt 的相应占位符。

- load_agent_profile(): 读取 <PROJECT_ROOT>/Helix.md 全文（mtime 缓存，
  文件修改后自动重新加载；缺失/读取失败时优雅降级返回空串，不抛异常）
- format_datetime_now(): 当前日期时间（默认 UTC+8，配置
  thinking.timezone_offset 可调；格式与 GetCurrentDateTimeTool 一致）
- format_location(): 默认地点（配置 default_location.city，中文 + 拼音）
- build_injections(): 组合以上三者，供 RPC 路由 / 未来 Thinking Channel 使用
"""

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict

from modules.utils.logger import log_warning
from modules.utils.paths import PROJECT_ROOT

# Helix.md 画像文件位于项目根目录（与 Helix.py / Helix.json 同级）
_PROFILE_FILE = os.path.join(PROJECT_ROOT, "Helix.md")

# 默认时区偏移小时数（可配置 thinking.timezone_offset 覆盖）
_DEFAULT_TZ_OFFSET_HOURS = 8

_WEEKDAY_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

_lock = threading.Lock()
_cache_mtime_ns: float = 0.0
_cache_text: str = ""


def load_agent_profile() -> str:
    """读取 Helix.md 全文（按 mtime 缓存，修改后自动重新读取）。

    文件不存在或读取失败时返回空串（调用方按默认画像提示词降级），
    不抛出异常、不拖垮宿主请求。
    """
    global _cache_mtime_ns, _cache_text
    try:
        mtime_ns = os.stat(_PROFILE_FILE).st_mtime_ns
    except OSError:
        # 文件不存在/不可访问 → 空画像（恢复文件后自动感知）
        return ""
    with _lock:
        if mtime_ns == _cache_mtime_ns:
            return _cache_text
        try:
            with open(_PROFILE_FILE, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            # 读取失败保留旧缓存（比返回空串更稳：至少有一段可用画像）
            log_warning(f"[helix_profile] Failed to read {_PROFILE_FILE}: {e}")
            return _cache_text
        _cache_mtime_ns = mtime_ns
        _cache_text = text
        return text


def _timezone_offset() -> int:
    """时区偏移小时数：配置 thinking.timezone_offset，默认 +8。"""
    try:
        from modules.config.config_manager import ConfigManager

        return int(ConfigManager().get("thinking.timezone_offset", _DEFAULT_TZ_OFFSET_HOURS))
    except Exception:
        return _DEFAULT_TZ_OFFSET_HOURS


def format_datetime_now() -> str:
    """格式化当前日期时间，格式与 GetCurrentDateTimeTool 保持一致。"""
    offset = _timezone_offset()
    now = datetime.now(timezone(timedelta(hours=offset)))
    sign = "+" if offset >= 0 else "-"
    return (
        f"日期时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"星期: {_WEEKDAY_CN[now.weekday()]}\n"
        f"时区: UTC{sign}{abs(offset)}"
    )


def format_location() -> str:
    """默认地点（Helix.json default_location.city，附拼音）；未配置返回空串。"""
    try:
        from modules.config.config_manager import ConfigManager

        city = (ConfigManager().get("default_location.city") or "").strip()
    except Exception:
        return ""
    if not city:
        return ""
    city_en = ""
    try:
        from plugins.common_tools import chinese_city_to_pinyin

        city_en = chinese_city_to_pinyin(city) or ""
    except Exception:
        city_en = ""
    return f"当前城市: {city} ({city_en})" if city_en else f"当前城市: {city}"


def build_injections() -> Dict[str, str]:
    """组合 Thinking 意图的固定注入上下文（Helix.md 画像 + 日期时间 + 地点）。

    返回值直接作为 process_request(context_injections=...) 传入；编排器仅
    透传这些纯文本到 Thinking 规划提示词模板的相应占位符。
    """
    return {
        "agent_profile": load_agent_profile(),
        "datetime": format_datetime_now(),
        "location": format_location(),
    }