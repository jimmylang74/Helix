"""页面/环境诊断程序：用 Playwright 复刻生产启动路径加载出错页面，分阶段打印调试信息。

定位"导航完成后整页冻结（evaluate/click/screenshot 全超时）"问题：
    0) 环境信息（代理变量/DNS/TCP/TLS/资源/残留浏览器进程）
    1) 浏览器启动 + 平凡页（data: URL）——验证渲染链路本身是否健康
    2) 携程移动首页（复刻 browser.py 移动模式 + 路由拦截）
    3) 飞猪结果页（桌面模式）
每阶段有界超时，单步失败不中断；冻结时用浏览器级 CDP + /proc 检查渲染进程状态，
并用网络层重放区分"渲染进程卡死"与"网络路径挂起"。

用法:
    python3 mcp/flight_search/diag_page.py                # 全部阶段
    python3 mcp/flight_search/diag_page.py --url <url>    # 只测指定页
    python3 mcp/flight_search/diag_page.py --rounds 3     # 同会话重复多轮(测第二轮才冻)
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

from playwright.sync_api import BrowserContext, Page, sync_playwright

signal.alarm(600)  # 总看门狗：10 分钟强制退出，避免诊断脚本自身挂死

CTRIP_URL = "https://m.ctrip.com/html5/flight/swift/index?flightWay=oneway"
FLIGGY_URL = (
    "https://sjipiao.fliggy.com/flight_search_result.htm?tripType=0"
    "&depCity=SHA&arrCity=CAN&depDate=2026-08-20"
    "&depCityName=%E4%B8%8A%E6%B5%B7&arrCityName=%E5%B9%BF%E5%B7%9E"
)
DIAG_DOMAINS = ["m.ctrip.com", "static.tripcdn.com", "webresource.c-ctrip.com", "sjipiao.fliggy.com"]

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
window.chrome = { runtime: {} };
"""
BLOCK_RESOURCE_TYPES = {"image", "media", "font"}
BLOCK_SDK_URL_PATTERNS = ("ubt.minh.js", "packages/ubt", "resaresonline", "ubtrms")


def say(tag: str, msg: str) -> None:
    print(f"[diag][{tag}] {msg}", flush=True)


def fmt_time(t0: float) -> str:
    return f"{time.time() - t0:.1f}s"


# ---------------------------------------------------------------- 阶段 0：环境信息
def env_info() -> None:
    say("env", f"python: {sys.version.split()[0]} | platform: {os.uname().nodename} {os.uname().sysname}")
    proxy = {k: v for k, v in os.environ.items() if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")}
    say("env", f"代理环境变量: {proxy if proxy else '无(直连)'}")
    try:
        with open("/proc/meminfo") as f:
            mem = {k: int(v.split()[0]) for k, v in (line.split(":", 1) for line in f if line.startswith(("MemTotal", "MemAvailable")))}
        say("env", f"内存: 总 {mem.get('MemTotal', 0) // 1024}MB / 可用 {mem.get('MemAvailable', 0) // 1024}MB")
    except OSError as e:
        say("env", f"内存读取失败: {e}")
    try:
        ulimit = subprocess.run(["bash", "-c", "ulimit -n"], capture_output=True, text=True, timeout=5).stdout.strip()
        say("env", f"ulimit -n (fd 上限): {ulimit}")
    except Exception as e:
        say("env", f"ulimit 读取失败: {e}")
    # 残留 chrome 进程（早期失败可能堆积僵尸浏览器，挤占内存）
    try:
        ps = subprocess.run(
            ["ps", "-e", "-o", "pid,ppid,rss,etime,cmd"], capture_output=True, text=True, timeout=5
        ).stdout
        chrs = [l for l in ps.splitlines() if re.search(r"chrom(e|ium)[\s-]", l) and "grep" not in l]
        say("env", f"现存 chrome 系进程: {len(chrs)} 个")
        for l in chrs[:15]:
            say("env", f"  {l.strip()[:150]}")
    except Exception as e:
        say("env", f"进程扫描失败: {e}")

    for host in DIAG_DOMAINS:
        # DNS 解析（带线程看门狗，避免 resolver 挂死拖垮诊断）
        result: list[str] = []
        def _resolve() -> None:
            try:
                addrs = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
                result.append(str(addrs[0][4][0]))
            except Exception as e:
                result.append(f"FAIL: {type(e).__name__}: {e}")
        t = time.time()
        th = _thread_resolve(host, _resolve)
        th.join(5)
        if th.is_alive():
            say("env", f"DNS {host}: 解析超时(>5s) !!")
            continue
        say("env", f"DNS {host}: {result[0]} ({fmt_time(t)})")
        # TCP + TLS 握手（纯 socket，不经浏览器，直接验证网络路径）
        try:
            t2 = time.time()
            with socket.create_connection((host, 443), timeout=5) as sock:
                tcp_t = time.time() - t2
                t3 = time.time()
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(sock, server_hostname=host) as ss:
                    tls_t = time.time() - t3
                    cert = ss.getpeercert() or {}
                    say("env", f"TCP/TLS {host}: 连接 {tcp_t:.1f}s + 握手 {tls_t:.1f}s | 证书CN={cert.get('subject', ((('CN', '?'),),))[0][0][1]}")
        except Exception as e:
            say("env", f"TCP/TLS {host}: FAIL {type(e).__name__}: {str(e)[:120]}")


import threading  # noqa: E402


def _thread_resolve(_host: str, fn: Any) -> threading.Thread:
    th = threading.Thread(target=fn, daemon=True)
    th.start()
    return th


# ---------------------------------------------------------------- 阶段 1/2/3：页面探测
class Probe:
    def __init__(self, ctx: BrowserContext, browser: Any) -> None:
        self.ctx = ctx
        self.browser = browser
        self.console: list[str] = []
        self.errors: list[str] = []
        self.failed_reqs: list[str] = []
        self.resp_status: dict[int, int] = {}
        self.aborted: list[str] = []

    def wire(self, page: Page, tag: str) -> None:
        page.on("console", lambda m: self.console.append(f"{m.type}:{m.text[:150]}"))
        page.on("pageerror", lambda e: self.errors.append(str(e)[:200]))
        page.on("requestfailed", lambda r: self.failed_reqs.append(f"{r.url[:120]} | {r.failure}"))

        def _on_response(r: Any) -> None:
            self.resp_status[r.status] = self.resp_status.get(r.status, 0) + 1

        page.on("response", _on_response)

        def _abort_heavy(route: Any) -> None:
            url = route.request.url
            if route.request.resource_type in BLOCK_RESOURCE_TYPES:
                route.abort()
            elif any(p in url for p in BLOCK_SDK_URL_PATTERNS):
                self.aborted.append(url[:120])
                route.abort()
            else:
                route.continue_()

        page.route("**/*", _abort_heavy)

    def report(self, tag: str) -> None:
        say(tag, f"console消息: {len(self.console)} 条 | 页面错误: {len(self.errors)} | 请求失败: {len(self.failed_reqs)}")
        say(tag, f"响应状态码分布: {self.resp_status or '无'}")
        if self.aborted:
            say(tag, f"被拦截的 SDK 请求: {len(self.aborted)} 个，首个: {self.aborted[0]}")
        for e in self.errors[:5]:
            say(tag, f"  页面错误: {e}")
        for fr in self.failed_reqs[:8]:
            say(tag, f"  请求失败: {fr}")

    def renderer_proc_info(self, tag: str) -> None:
        """浏览器级 CDP（由 browser 进程服务，渲染进程冻结时通常仍可用）+ /proc 线程状态。"""
        try:
            cdp = self.browser.new_browser_cdp_session()
            info = cdp.send("SystemInfo.getProcessInfo")
            procs = sorted(info.get("processInfo", []), key=lambda p: p.get("cpuUsage", 0), reverse=True)
            for p in procs[:10]:
                pid = p.get("id", 0)
                say(tag, f"CDP进程 {p.get('type')} pid={pid} cpuUsage={(p.get('cpuUsage') or 0) * 100:.1f}%")
                if p.get("type") in ("renderer", "utility", "gpu"):
                    self._thread_state(pid, tag)
        except Exception as e:
            say(tag, f"浏览器级CDP失败(可能整个浏览器都卡了): {type(e).__name__}: {str(e)[:120]}")

    def _thread_state(self, pid: int, tag: str) -> None:
        try:
            out = subprocess.run(
                ["ps", "-L", "-p", str(pid), "-o", "tid,%cpu,stat,wchan:24", "--sort=-%cpu"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            lines = [l for l in out.splitlines() if l.strip() and not l.startswith("TID")]
            main = next((l for l in lines if l.strip().split()[0] == str(pid)), None)
            say(tag, f"  线程(pid={pid}) 主线程: {main if main else '未找到主线程'}")
            for l in lines[:5]:
                say(tag, f"    {l.strip()}")
        except Exception as e:
            say(tag, f"  /proc 线程读取失败(pid={pid}): {e}")

    def network_replay(self, page: Page, tag: str) -> None:
        """驱动端网络重放：不依赖渲染进程，冻结时也能拿服务器响应。"""
        try:
            t = time.time()
            resp = self.ctx.request.get(page.url, timeout=20000)
            body = resp.body()
            say(tag, f"网络重放 {page.url[:80]}: HTTP {resp.status}, {len(body)} 字节 ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"网络重放失败: {type(e).__name__}: {str(e)[:120]}")


def probe_page(ctx: BrowserContext, browser: Any, tag: str, url: str, mobile: bool) -> None:
    t0 = time.time()
    say(tag, f"===== 探测 {tag}: {url}")
    probe = Probe(ctx, browser)
    page: Page | None = None
    try:
        page = ctx.new_page()
        probe.wire(page, tag)
        t = time.time()
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            say(tag, f"goto 完成: {page.url[:100]} ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"goto 异常: {type(e).__name__}: {str(e)[:120]} 当前URL: {page.url[:100] if page else '?'}")
        # 渲染/执行链路是否可用：有界 evaluate
        t = time.time()
        try:
            title = page.wait_for_function("() => document.title", timeout=8000).json_value()
            say(tag, f"evaluate 可用: title={title!r} ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"!! evaluate 超时(渲染主线程疑似冻结): {type(e).__name__} ({fmt_time(t)})")
        # 截图（CDP 级渲染链路）
        t = time.time()
        try:
            page.screenshot(path=f"catch/diag_{tag}.png", timeout=5000)
            say(tag, f"screenshot 可用 ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"!! screenshot 超时: {type(e).__name__}: {str(e)[:80]}")
        probe.renderer_proc_info(tag)
        if "evaluate 超时" in "":  # 占位：实际以下面状态判断
            pass
        probe.report(tag)
        if page.url.startswith("http"):
            probe.network_replay(page, tag)
    except Exception as e:
        say(tag, f"!! 探测异常: {type(e).__name__}: {str(e)[:150]}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                say(tag, "page.close 失败(渲染进程无响应)，将随浏览器一并关闭")
        say(tag, f"===== 探测结束，总耗时 {fmt_time(t0)}")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    url_only: str | None = None
    rounds = 1
    if "--url" in args:
        url_only = args[args.index("--url") + 1]
    if "--rounds" in args:
        rounds = int(args[args.index("--rounds") + 1])

    env_info()
    say("main", f"playwright import OK, rounds={rounds}")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--lang=zh-CN",
                ],
            )
            say("main", f"浏览器启动 OK, executable={pw.chromium.executable_path}")
        except Exception as e:
            say("main", f"!! 浏览器启动失败(环境问题): {type(e).__name__}: {str(e)[:200]}")
            return

        # 阶段 1：平凡页（本地 data: URL，无网络）——验证渲染链路本身
        try:
            ctx0 = browser.new_context(
                locale="zh-CN", timezone_id="Asia/Shanghai",
                viewport={"width": 1440, "height": 900},
            )
            ctx0.add_init_script(STEALTH_JS)
            p = ctx0.new_page()
            t = time.time()
            p.goto("data:text/html,<title>diag-local</title><h1>ok</h1>", timeout=15000)
            title = p.wait_for_function("() => document.title", timeout=5000).json_value()
            p.screenshot(path="catch/diag_local.png", timeout=5000)
            say("local", f"平凡页 OK: title={title!r} 全部链路可用 ({fmt_time(t)})")
            ctx0.close()
        except Exception as e:
            say("local", f"!! 平凡页失败——渲染/浏览器链路本身有问题: {type(e).__name__}: {str(e)[:150]}")

        # 阶段 2/3：真实页面（同一浏览器会话，多轮可测第二轮才冻）
        mobile_ctx = browser.new_context(
            locale="zh-CN", timezone_id="Asia/Shanghai",
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"),
            is_mobile=True, has_touch=True, viewport={"width": 390, "height": 844},
        )
        mobile_ctx.add_init_script(STEALTH_JS)
        desktop_ctx = browser.new_context(
            locale="zh-CN", timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
        )
        desktop_ctx.add_init_script(STEALTH_JS)

        urls = [(CTRIP_URL, mobile_ctx, "ctrip")] + ([] if url_only else [(FLIGGY_URL, desktop_ctx, "fliggy")])
        if url_only:
            urls = [(url_only, desktop_ctx, "custom")]
        for r in range(1, rounds + 1):
            say("main", f"----------- 第 {r}/{rounds} 轮 -----------")
            for url, ctx, tag in urls:
                probe_page(ctx, browser, tag, url, mobile=(tag == "ctrip"))
        mobile_ctx.close()
        desktop_ctx.close()
        browser.close()
    say("main", "诊断结束")


if __name__ == "__main__":
    main()
