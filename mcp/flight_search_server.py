#!/usr/bin/env python3
"""
内部 MCP Server: 机票价格搜索（Streamable HTTP, Stateless, JSON 响应）。

基于 mcp pip 包（FastMCP），使用 Streamable HTTP Transport（Stateless），
返回 JSON 格式的 list/tools 与 tools/call 结果。

工具:
    search_flights(出发城市, 目的城市, 日期) -> JSON
    通过 Playwright 爬取携程与飞猪的航班价格，输出航班列表
    （航班号 / 起飞时间 / 出发城市 / 目的城市 / 价格 / 来源）。

启动:
    python3 mcp/flight_search_server.py            # 默认 0.0.0.0:8003
    FLIGHT_MCP_HOST=127.0.0.1 FLIGHT_MCP_PORT=8003 python3 mcp/flight_search_server.py

    # 或通过 uvicorn（模块路径依赖当前目录为 mcp/）:
    cd mcp && uvicorn flight_search_server:app --host 0.0.0.0 --port 8003

在 Helix.json 中注册:
    "flight_search": {"type": "server", "enabled": true, "url": "http://<host>:8003/mcp"}

依赖:
    pip install mcp playwright uvicorn
    python3 -m playwright install chromium
"""

import asyncio
import json
import os
import sys
from typing import Any, Optional

# 保证 `python3 mcp/flight_search_server.py` 与 `uvicorn` 两种方式都能导入 flight_search 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP  # noqa: E402

try:
    # 以模块方式导入（如从工作区根目录 uvicorn mcp.flight_search_server:app）
    from .flight_search.cities import to_iata
    from .flight_search.ctrip import search_ctrip
    from .flight_search.fliggy import search_fliggy
except ImportError:
    # 直接以脚本方式运行（python3 mcp/flight_search_server.py）
    from flight_search.cities import to_iata  # pyright: ignore[reportImplicitRelativeImport]
    from flight_search.ctrip import search_ctrip  # pyright: ignore[reportImplicitRelativeImport]
    from flight_search.fliggy import search_fliggy  # pyright: ignore[reportImplicitRelativeImport]

HOST = os.environ.get("FLIGHT_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("FLIGHT_MCP_PORT", "8003"))

mcp = FastMCP(
    "flight-search",
    instructions=(
        "机票价格搜索：通过 Playwright 爬取携程（ctrip.com）与飞猪（fliggy.com）"
        "的航班价格，返回航班列表 JSON。"
    ),
    stateless_http=True,   # Streamable HTTP 无状态模式：每个请求独立，无会话跟踪
    json_response=True,    # 响应使用 JSON 而非 SSE 流
)


@mcp.tool(description="搜索航班价格")
async def search_flights(出发城市: str, 目的城市: str, 日期: str) -> str:
    """搜索指定日期从出发城市到目的城市的航班价格。

    Args:
        出发城市: 出发城市名（如"上海"）或三字码（如"SHA"）
        目的城市: 目的城市名（如"北京"）或三字码（如"BJS"）
        日期: 出发日期，格式 YYYY-MM-DD
    """
    import time as _time

    _t0 = _time.time()
    print(f"[flight] ===== tools/call search_flights 开始: {出发城市} -> {目的城市} {日期} =====", flush=True)

    result: dict[str, Any] = {
        "查询": {
            "出发城市": 出发城市.strip(),
            "目的城市": 目的城市.strip(),
            "日期": 日期.strip(),
        },
        "航班": [],
    }
    errors: list[dict[str, str]] = []

    try:
        dep_iata = to_iata(出发城市)
        arr_iata = to_iata(目的城市)
        print(f"[flight] 城市解析: {出发城市} -> {dep_iata}, {目的城市} -> {arr_iata}", flush=True)
    except ValueError as e:
        print(f"[flight] 城市解析失败: {e}", flush=True)
        return json.dumps({"查询": result["查询"], "错误": [{"说明": str(e)}]}, ensure_ascii=False)

    dep_city = 出发城市.strip()
    arr_city = 目的城市.strip()

    # 同步 Playwright 爬虫需在独立线程执行（FastMCP 在事件循环内调用工具函数）
    # 携程
    _t1 = _time.time()
    print(f"[flight] [携程] 开始爬取 {dep_iata}->{arr_iata} {日期}", flush=True)
    ctrip_flights, ctrip_err = await asyncio.to_thread(
        search_ctrip, dep_iata, arr_iata, 日期.strip(), dep_city, arr_city
    )
    print(f"[flight] [携程] 结束，耗时 {_time.time()-_t1:.1f}s，航班 {len(ctrip_flights)} 条，错误: {ctrip_err}", flush=True)
    if ctrip_err:
        errors.append({"来源": "携程", "错误": ctrip_err})
    result["航班"].extend(ctrip_flights)

    # 飞猪
    _t2 = _time.time()
    print(f"[flight] [飞猪] 开始爬取 {dep_city}->{arr_city} {日期}", flush=True)
    fliggy_flights, fliggy_err = await asyncio.to_thread(search_fliggy, dep_city, arr_city, 日期.strip())
    print(f"[flight] [飞猪] 结束，耗时 {_time.time()-_t2:.1f}s，航班 {len(fliggy_flights)} 条，错误: {fliggy_err}", flush=True)
    if fliggy_err:
        errors.append({"来源": "飞猪", "错误": fliggy_err})
    result["航班"].extend(fliggy_flights)

    if errors:
        result["错误"] = errors

    print(f"[flight] ===== tools/call search_flights 完成，总耗时 {_time.time()-_t0:.1f}s，航班 {len(result['航班'])} 条 ===== ", flush=True)
    return json.dumps(result, ensure_ascii=False)


app = mcp.streamable_http_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
