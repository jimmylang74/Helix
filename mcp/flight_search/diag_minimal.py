"""最简参数对照诊断：仅 headless=True 启动浏览器（不加任何 args/隐身/路由拦截）。

与 diag_page.py（复刻 browser.py 全套参数）A/B 对照：
    - full 冻结、minimal 正常 -> 问题出在启动参数/特征代码（隐身JS/路由拦截/移动模式等）
    - full 与 minimal 都冻结     -> 环境级问题（网络路径/浏览器二进制/系统资源）
    - minimal 也冻结但平凡页正常  -> 站点页面与该浏览器组合的问题

用法:
    python3 mcp/flight_search/diag_minimal.py
    python3 mcp/flight_search/diag_minimal.py --url <url>
"""

import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

signal.alarm(600)  # 总看门狗：10 分钟强制退出

CTRIP_URL = "https://m.ctrip.com/html5/flight/swift/index?flightWay=oneway"
FLIGGY_URL = (
    "https://sjipiao.fliggy.com/flight_search_result.htm?tripType=0"
    "&depCity=SHA&arrCity=CAN&depDate=2026-08-20"
    "&depCityName=%E4%B8%8A%E6%B5%B7&arrCityName=%E5%B9%BF%E5%B7%9E"
)


def say(tag: str, msg: str) -> None:
    print(f"[diag-min][{tag}] {msg}", flush=True)


def fmt_time(t0: float) -> str:
    return f"{time.time() - t0:.1f}s"


def probe(ctx: BrowserContext, browser: Browser, tag: str, url: str) -> None:
    t0 = time.time()
    say(tag, f"===== 探测 {tag}: {url}")
    console_msgs: list[str] = []
    errors: list[str] = []
    failed: list[str] = []
    statuses: dict[int, int] = {}
    page: Page | None = None
    try:
        page = ctx.new_page()

        def _on_response(r: Any) -> None:
            statuses[r.status] = statuses.get(r.status, 0) + 1

        page.on("console", lambda m: console_msgs.append(f"{m.type}:{m.text[:150]}"))
        page.on("pageerror", lambda e: errors.append(str(e)[:200]))
        page.on("requestfailed", lambda r: failed.append(f"{r.url[:120]} | {r.failure}"))
        page.on("response", _on_response)

        t = time.time()
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            say(tag, f"goto 完成: {page.url[:100]} ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"goto 异常: {type(e).__name__}: {str(e)[:120]} 当前URL: {page.url[:100] if page else '?'}")

        t = time.time()
        try:
            title = page.wait_for_function("() => document.title", timeout=8000).json_value()
            say(tag, f"evaluate 可用: title={title!r} ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"!! evaluate 超时(渲染主线程疑似冻结): {type(e).__name__} ({fmt_time(t)})")

        t = time.time()
        try:
            page.screenshot(path=f"catch/diag_min_{tag}.png", timeout=5000)
            say(tag, f"screenshot 可用 ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"!! screenshot 超时: {type(e).__name__}: {str(e)[:80]}")

        try:
            cdp = browser.new_browser_cdp_session()
            info = cdp.send("SystemInfo.getProcessInfo")
            procs = sorted(info.get("processInfo", []), key=lambda p: p.get("cpuUsage", 0), reverse=True)
            for p in procs[:8]:
                pid = p.get("id", 0)
                say(tag, f"CDP进程 {p.get('type')} pid={pid} cpuUsage={(p.get('cpuUsage') or 0) * 100:.1f}%")
                if p.get("type") == "renderer":
                    try:
                        out = subprocess.run(
                            ["ps", "-L", "-p", str(pid), "-o", "tid,%cpu,stat,wchan:24", "--sort=-%cpu"],
                            capture_output=True, text=True, timeout=5,
                        ).stdout
                        lines = [l for l in out.splitlines() if l.strip() and not l.startswith("TID")]
                        main = next((l for l in lines if l.strip().split()[0] == str(pid)), None)
                        say(tag, f"  renderer主线程: {main if main else '未找到'}")
                    except Exception as e:
                        say(tag, f"  /proc 线程读取失败: {e}")
        except Exception as e:
            say(tag, f"浏览器级CDP失败: {type(e).__name__}: {str(e)[:120]}")

        say(tag, f"console: {len(console_msgs)} 条 | 页面错误: {len(errors)} | 请求失败: {len(failed)}")
        say(tag, f"响应状态码: {statuses or '无'}")
        for e in errors[:5]:
            say(tag, f"  页面错误: {e}")
        for fr in failed[:8]:
            say(tag, f"  请求失败: {fr}")

        if page.url.startswith("http"):
            try:
                t = time.time()
                resp = ctx.request.get(page.url, timeout=20000)
                say(tag, f"网络重放: HTTP {resp.status}, {len(resp.body())} 字节 ({fmt_time(t)})")
            except Exception as e:
                say(tag, f"网络重放失败: {type(e).__name__}: {str(e)[:120]}")
    except Exception as e:
        say(tag, f"!! 探测异常: {type(e).__name__}: {str(e)[:150]}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                say(tag, "page.close 失败(渲染进程无响应)")
        say(tag, f"===== 探测结束，总耗时 {fmt_time(t0)}")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    url_only: str | None = None
    if "--url" in args:
        url_only = args[args.index("--url") + 1]

    proxy = {k: v for k, v in os.environ.items() if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")}
    say("env", f"代理环境变量: {proxy if proxy else '无(直连)'}")
    try:
        chrs = [
            l for l in subprocess.run(
                ["ps", "-e", "-o", "pid,cmd"], capture_output=True, text=True, timeout=5
            ).stdout.splitlines()
            if re.search(r"chrom(e|ium)[\s-]", l) and "grep" not in l
        ]
        say("env", f"现存 chrome 系进程: {len(chrs)} 个")
    except Exception:
        pass

    say("main", "最简参数模式: chromium.launch(headless=True) 无任何自定义 args")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
            say("main", f"浏览器启动 OK, executable={pw.chromium.executable_path}")
        except Exception as e:
            say("main", f"!! 浏览器启动失败(环境问题): {type(e).__name__}: {str(e)[:200]}")
            return

        # 平凡页（本地 data: URL）先验证渲染链路
        try:
            ctx0 = browser.new_context()
            p = ctx0.new_page()
            t = time.time()
            p.goto("data:text/html,<title>diag-local</title><h1>ok</h1>", timeout=15000)
            title = p.wait_for_function("() => document.title", timeout=5000).json_value()
            p.screenshot(path="catch/diag_min_local.png", timeout=5000)
            say("local", f"平凡页 OK: title={title!r} 全部链路可用 ({fmt_time(t)})")
            ctx0.close()
        except Exception as e:
            say("local", f"!! 平凡页失败——渲染/浏览器链路本身有问题: {type(e).__name__}: {str(e)[:150]}")

        desktop_ctx = browser.new_context()
        urls = [(CTRIP_URL, "ctrip")] if url_only else [(CTRIP_URL, "ctrip"), (FLIGGY_URL, "fliggy")]
        if url_only:
            urls = [(url_only, "custom")]
        for url, tag in urls:
            probe(desktop_ctx, browser, tag, url)
        desktop_ctx.close()
        browser.close()
    say("main", "诊断结束")


if __name__ == "__main__":
    main()
