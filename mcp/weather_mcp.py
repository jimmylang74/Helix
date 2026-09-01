#!/usr/bin/env python3
"""
weather_mcp.py — Open-Meteo 天气查询 MCP 服务端（Helix 内置 MCP 服务）

独立可运行的 MCP Server 组件，采用 Streamable HTTP（Stateless）传输，
Tools/List 调用均返回纯 JSON 格式（非 SSE 包装）。

特性:
- 默认端口 8004, 支持 --port 参数指定
- 根据查询日期与当前日期对比自动选择接口, 返回统一 JSON 格式:
    * 今天及未来 7 天内 → /v1/forecast  预报接口
    * 更早的历史日期    → /v1/archive   历史数据接口
- 中文/英文城市名自动地理编码 (geocoding-api.open-meteo.com)
- 网络代理: 默认遵循 HTTPS_PROXY/HTTP_PROXY 环境变量, 亦可 --proxy 显式指定

用法:
    python3 mcp/weather_mcp.py [--port 8004] [--host 127.0.0.1] [--proxy http://host:port]
"""

import argparse
import ctypes
import json
import os
import signal
import sys
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ── Open-Meteo 接口 ──────────────────────────────────────────────
API_BASE = "https://api.open-meteo.com/v1/forecast"               # 当天及 7 天内预报
API_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"     # 历史数据
API_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"    # 城市地理编码

FORECAST_RANGE_DAYS = 7           # 今天 + 未来 7 天
FALLBACK_TIMEZONE = "Asia/Shanghai"
REQUEST_TIMEOUT = 20

# WMO 天气代码 → (中文描述, 英文描述)  https://open-meteo.com/en/docs 附录
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("晴", "Clear sky"),
    1: ("基本晴朗", "Mainly clear"),
    2: ("局部多云", "Partly cloudy"),
    3: ("阴", "Overcast"),
    45: ("雾", "Fog"),
    48: ("冻雾", "Depositing rime fog"),
    51: ("小毛毛雨", "Light drizzle"),
    53: ("毛毛雨", "Drizzle"),
    55: ("浓毛毛雨", "Dense drizzle"),
    56: ("冻毛毛雨", "Light freezing drizzle"),
    57: ("浓冻毛毛雨", "Dense freezing drizzle"),
    61: ("小雨", "Slight rain"),
    63: ("中雨", "Moderate rain"),
    65: ("大雨", "Heavy rain"),
    66: ("冻雨", "Light freezing rain"),
    67: ("强冻雨", "Heavy freezing rain"),
    71: ("小雪", "Slight snow fall"),
    73: ("中雪", "Moderate snow fall"),
    75: ("大雪", "Heavy snow fall"),
    77: ("雪粒", "Snow grains"),
    80: ("小阵雨", "Slight rain showers"),
    81: ("中阵雨", "Moderate rain showers"),
    82: ("强阵雨", "Violent rain showers"),
    85: ("小阵雪", "Slight snow showers"),
    86: ("大阵雪", "Heavy snow showers"),
    95: ("雷暴", "Thunderstorm"),
    96: ("雷暴伴小冰雹", "Thunderstorm with slight hail"),
    99: ("雷暴伴大冰雹", "Thunderstorm with heavy hail"),
}

# 预报接口可用的 daily 变量（archive 接口变量名与之一致, 保证统一格式）
DAILY_VARS = [
    "weather_code",
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
    "apparent_temperature_max", "apparent_temperature_min",
    "precipitation_probability_max",
    "precipitation_sum", "rain_sum", "snowfall_sum",
    "wind_speed_10m_max", "wind_gusts_10m_max", "wind_direction_10m_dominant",
    "uv_index_max",
    "sunrise", "sunset",
]
# 预报接口可用的 current 变量
CURRENT_VARS = [
    "temperature_2m", "relative_humidity_2m", "apparent_temperature",
    "weather_code", "is_day", "precipitation",
    "wind_speed_10m", "wind_direction_10m",
]

_session: requests.Session = requests.Session()          # trust_env=True: 自动遵循环境代理
_geo_cache: dict[str, dict[str, Any]] = {}


# ── HTTP 辅助 ─────────────────────────────────────────────────────

def _configure_session(proxy: str = "") -> None:
    """设置网络代理。显式指定优先于环境变量。"""
    global _session
    _session = requests.Session()
    if proxy:
        _session.proxies.update({"http": proxy, "https": proxy})


def _geocode(city: str) -> dict[str, Any] | None:
    """城市名 → 经纬度/时区等地理信息（带缓存）。"""
    key = city.strip()
    if not key:
        return None
    if key in _geo_cache:
        return _geo_cache[key]
    try:
        resp = _session.get(
            API_GEOCODE,
            params={"name": key, "count": 1, "language": "zh", "format": "json"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        results = (resp.json() or {}).get("results") or []
    except Exception:
        results = []
    if not results:
        return None
    r = results[0]
    geo = {
        "name": r.get("name") or key,
        "country": r.get("country") or "",
        "admin1": r.get("admin1") or "",
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
        "elevation": r.get("elevation"),
        "timezone": r.get("timezone") or FALLBACK_TIMEZONE,
    }
    _geo_cache[key] = geo
    return geo


def _local_today(tz_name: str) -> date:
    """指定时区的“今天”日期。"""
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(ZoneInfo(FALLBACK_TIMEZONE)).date()


def _parse_date(text: str) -> date | None:
    """解析 YYYY-MM-DD 日期。"""
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _query_open_meteo(url: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _pick_daily(resp: dict[str, Any], target: date, daily_vars: list[str]) -> dict[str, Any] | None:
    """从响应中摘取目标日期的 daily 数据; 无该日数据返回 None。"""
    daily = resp.get("daily") or {}
    times = daily.get("time") or []
    try:
        idx = times.index(target.isoformat())
    except ValueError:
        return None
    return {k: daily[k][idx] for k in daily_vars if daily.get(k) is not None and idx < len(daily[k])}


def _wmo_zh(code: Any) -> str:
    try:
        return WMO_CODES.get(int(code), (str(code), ""))[0]
    except (TypeError, ValueError):
        return "未知"


def _build_summary(geo: dict[str, Any], target: date, daily: dict[str, Any], units: dict[str, Any], current: dict[str, Any] | None, date_type: str) -> str:
    """组成一句话中文天气摘要。"""
    parts: list[str] = []
    parts.append(f"{geo['name']} {target.isoformat()} 星期{'一二三四五六日'[target.weekday()]}")
    if date_type == "历史数据":
        parts.append("历史天气")
    w = _wmo_zh(daily.get("weather_code"))
    parts.append(w)
    if daily.get("temperature_2m_max") is not None:
        parts.append(f"最高 {daily['temperature_2m_max']}{units.get('temperature_2m_max', '°C')}")
    if daily.get("temperature_2m_min") is not None:
        parts.append(f"最低 {daily['temperature_2m_min']}{units.get('temperature_2m_min', '°C')}")
    if daily.get("precipitation_probability_max") is not None and date_type != "历史数据":
        parts.append(f"降水概率 {daily['precipitation_probability_max']}{units.get('precipitation_probability_max', '%')}")
    if daily.get("precipitation_sum") is not None and daily["precipitation_sum"]:
        parts.append(f"降水量 {daily['precipitation_sum']}{units.get('precipitation_sum', 'mm')}")
    if daily.get("wind_speed_10m_max") is not None:
        parts.append(f"最大风速 {daily['wind_speed_10m_max']}{units.get('wind_speed_10m_max', 'km/h')}")
    if daily.get("uv_index_max") is not None:
        parts.append(f"紫外线 {daily['uv_index_max']}")
    if current and current.get("temperature_2m") is not None:
        parts.append(f"当前 {current['temperature_2m']}{units.get('temperature_2m', '°C')}")
    return "，".join(parts)


# ── MCP 工具 ──────────────────────────────────────────────────────

def _run_weather(city: str, date_text: str = "") -> dict[str, Any]:
    """核心查询逻辑: 地理编码 → 日期分流 → 调用 Open-Meteo → 统一 JSON。"""
    geo = _geocode(city)
    if not geo:
        return {"success": False, "error": f"未找到城市「{city}」的经纬度信息，请检查城市名称"}

    today = _local_today(geo["timezone"])

    if date_text.strip():
        target = _parse_date(date_text)
        if target is None:
            return {
                "success": False,
                "city": geo["name"],
                "error": f"日期格式不正确: {date_text!r}，请使用 YYYY-MM-DD（如 2026-08-31）",
            }
    else:
        target = today

    delta = (target - today).days
    lat, lon = geo["latitude"], geo["longitude"]
    tz = geo["timezone"]

    if delta < 0:
        # 历史数据 → archive 接口
        endpoint = "archive"
        date_type = "历史数据"
        base = {
            "success": True,
            "endpoint": endpoint,
            "city": geo["name"], "country": geo["country"] or None, "admin1": geo["admin1"] or None,
            "latitude": lat, "longitude": lon, "timezone": tz, "elevation": geo["elevation"],
            "query_date": target.isoformat(), "weekday": "星期" + "一二三四五六日"[target.weekday()],
            "date_type": date_type,
        }
        try:
            resp = _query_open_meteo(API_ARCHIVE, {
                "latitude": lat, "longitude": lon,
                "start_date": target.isoformat(), "end_date": target.isoformat(),
                "daily": ",".join(DAILY_VARS),
                "timezone": tz,
            })
        except Exception as e:
            return {**base, "success": False, "error": f"历史天气数据请求失败: {e}"}
        daily = _pick_daily(resp, target, DAILY_VARS)
        if not daily:
            return {
                **base, "success": False,
                "error": f"该日期（{target.isoformat()}）暂无历史天气数据，可查询的日期范围有限",
            }
        base.update(daily)
        base["weather"] = _wmo_zh(daily.get("weather_code"))
        base["summary"] = _build_summary(geo, target, daily, resp.get("daily_units") or {}, None, date_type)
        return base

    if delta <= FORECAST_RANGE_DAYS:
        # 今天及未来 7 天内 → forecast 接口
        endpoint = "forecast"
        date_type = "今天" if delta == 0 else "未来预报（今天及未来7天内）"
        base = {
            "success": True,
            "endpoint": endpoint,
            "city": geo["name"], "country": geo["country"] or None, "admin1": geo["admin1"] or None,
            "latitude": lat, "longitude": lon, "timezone": tz, "elevation": geo["elevation"],
            "query_date": target.isoformat(), "weekday": "星期" + "一二三四五六日"[target.weekday()],
            "date_type": date_type,
        }
        params: dict[str, Any] = {
            "latitude": lat, "longitude": lon,
            "daily": ",".join(DAILY_VARS),
            "timezone": tz,
            "forecast_days": delta + 1,  # forecast_days 从今天起算
        }
        if delta == 0:
            params["current"] = ",".join(CURRENT_VARS)
        try:
            resp = _query_open_meteo(API_BASE, params)
        except Exception as e:
            return {**base, "success": False, "error": f"天气预报请求失败: {e}"}
        daily = _pick_daily(resp, target, DAILY_VARS)
        if not daily:
            return {**base, "success": False, "error": f"接口未返回 {target.isoformat()} 的预报数据"}
        base.update(daily)
        base["weather"] = _wmo_zh(daily.get("weather_code"))
        current = None
        if delta == 0:
            cur = resp.get("current") or {}
            current = {k: cur[k] for k in CURRENT_VARS if k in cur}
            if current:
                base["current"] = current
        base["summary"] = _build_summary(geo, target, daily, resp.get("daily_units") or {}, current, date_type)
        return base

    return {
        "success": False,
        "city": geo["name"],
        "error": f"只能查询今天及未来 {FORECAST_RANGE_DAYS} 天内的预报天气，{target.isoformat()} 超出预报范围",
    }


def build_server() -> FastMCP:
    mcp = FastMCP(
        "weather",
        json_response=True,     # List/Tools 返回纯 JSON（非 SSE）
        stateless_http=True,    # Streamable HTTP Stateless 传输
    )

    @mcp.tool()
    def weather(
        city: Annotated[str, Field(description="城市名称，支持中文（如：南京、北京、上海）或英文（如：London）")],
        date: Annotated[str, Field(description="查询日期，格式 YYYY-MM-DD；留空表示查询今天。今天及未来 7 天内走预报接口，更早的日期走历史数据接口")] = "",
    ) -> str:
        """查询指定城市在指定日期的天气情况（基于 Open-Meteo，返回统一 JSON 格式）。"""
        payload = _run_weather(city, date)
        return json.dumps(payload, ensure_ascii=False)

    return mcp


# ── 入口 ──────────────────────────────────────────────────────────

def _set_pdeathsig() -> None:
    """Linux: Helix（父进程）死亡时由内核自动向本进程发送 SIGTERM。

    配合 mcp_registry 的 start_new_session=True 拉起方式，即使 Helix 被
    无法捕获的信号（如 SIGKILL）杀死，本服务也会收到 SIGTERM 优雅退出，
    不会残留为孤儿进程。非 Linux 平台静默降级（由 Helix 侧 shutdown 兜底）。
    """
    PR_SET_PDEATHSIG = 1
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
        # 竞态防护：若设置前父进程已消亡（被 init 收养），立即退出而非残留。
        # 仅当确由 Helix 拉起（设了 MCP_SERVER_NAME）时生效，独立手动运行不受影响。
        if os.getppid() == 1 and os.environ.get("MCP_SERVER_NAME"):
            sys.exit(1)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="weather_mcp",
        description="Open-Meteo 天气查询 MCP Server（Streamable HTTP, Stateless, 纯 JSON）",
    )
    parser.add_argument("--port", type=int, default=8004, help="服务端口（默认 8004）")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument(
        "--proxy", type=str, default="",
        help="HTTP/HTTPS 代理地址（如 http://192.168.10.2:7890）；缺省时遵循 HTTPS_PROXY/HTTP_PROXY 环境变量",
    )
    args = parser.parse_args()

    _set_pdeathsig()

    _configure_session(args.proxy)

    mcp = build_server()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())