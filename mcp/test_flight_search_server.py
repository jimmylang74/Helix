#!/usr/bin/env python3
"""
针对 mcp/flight_search_server.py 的测试程序。

通过 MCP Streamable HTTP 客户端连接正在运行的 flight_search MCP Server，
调用 search_flights 工具（出发城市 / 目的城市 / 日期），并将航班结果
以列表形式逐条打印（非 JSON 原始输出）。

用法:
    # 1. 先启动服务（默认 0.0.0.0:8003）:
    #    python3 mcp/flight_search_server.py
    #
    # 2. 再运行测试（出发城市默认"南京"，目的城市与日期必填）:
    python3 mcp/test_flight_search_server.py --date 2026-08-20 --dst 北京
    python3 mcp/test_flight_search_server.py --dep 上海 --date 2026-08-20 --dst 广州
    python3 mcp/test_flight_search_server.py --url http://127.0.0.1:8003/mcp --dep 南京 --date 2026-08-20 --dst 成都

依赖:
    pip install mcp httpx
"""

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_URL = "http://127.0.0.1:8003/mcp"
# 携程 + 飞猪两次 Playwright 爬取耗时较长，客户端超时需放宽
CLIENT_TIMEOUT_SECONDS = 600


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="flight_search MCP Server 测试程序：调用 search_flights 工具"
    )
    parser.add_argument(
        "--dep", "--出发城市",
        default="南京",
        help="出发城市名或三字码（默认: 南京）",
    )
    parser.add_argument(
        "--dst", "--目的城市",
        required=True,
        help="目的城市名或三字码（必填）",
    )
    parser.add_argument(
        "--date", "--日期",
        required=True,
        help="出发日期，格式 YYYY-MM-DD（必填）",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"MCP Server 地址（默认: {DEFAULT_URL}）",
    )
    return parser.parse_args()


def print_result_list(payload: dict[str, Any]) -> None:
    """将 search_flights 返回的 JSON 以列表形式打印。"""
    query = payload.get("查询", {})
    flights = payload.get("航班", [])
    print(
        f"===== 航班查询: {query.get('出发城市', '')} -> {query.get('目的城市', '')} "
        f"{query.get('日期', '')} ====="
    )
    if not flights:
        print("未查询到航班")
    else:
        print(f"共 {len(flights)} 条航班:\n")
        for i, f in enumerate(flights, 1):
            price = f.get("价格")
            price_str = "-" if price is None else f"¥{price}"
            print(
                f"  {i:>2}. [{f.get('航班号', '')}] 起飞 {f.get('起飞时间', '')}  "
                f"{f.get('出发城市', '')} -> {f.get('目的城市', '')}  {price_str}  "
                f"(来源: {f.get('来源', '')})"
            )
    errors = payload.get("错误")
    if errors:
        print("\n错误:")
        for e in errors:
            print(f"  - {e.get('来源', '')}: {e.get('错误', '')}")


async def call_search_flights(url: str, dep: str, dst: str, date: str) -> None:
    """连接 MCP Server 并调用 search_flights，打印 JSON 结果。"""
    timeout = httpx.Timeout(CLIENT_TIMEOUT_SECONDS, connect=10)
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                print(
                    f"[test] 调用 search_flights(出发城市={dep}, 目的城市={dst}, 日期={date})",
                    flush=True,
                )
                result = await session.call_tool(
                    "search_flights",
                    {"出发城市": dep, "目的城市": dst, "日期": date},
                )
                if result.isError:
                    print("[test] 工具返回错误，内容如下：", file=sys.stderr)
                for block in result.content:
                    text = getattr(block, "text", None)
                    if text is None:
                        continue
                    try:
                        payload = json.loads(text)
                        print_result_list(payload)
                    except json.JSONDecodeError:
                        print(text)


def main() -> None:
    args = parse_args()
    print(f"[test] 连接 {args.url}", flush=True)
    asyncio.run(call_search_flights(args.url, args.dep, args.dst, args.date))


if __name__ == "__main__":
    main()
