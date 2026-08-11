"""启动配置二分诊断：同一页面在 4 种浏览器启动配置下的行为对比。

目标：定位"平凡页 screenshot 也冻结"的根因变量（launch args / stealth JS）。
    cfg0 默认            = chromium.launch(headless=True)                     (已知正常)
    cfg1 args            = 默认 + browser.py 的 4 个 launch args
    cfg2 stealth         = 默认 + STEALTH_JS 注入
    cfg3 full            = args + stealth + 路由拦截 + 携程用移动上下文      (复刻生产冻结)

每种配置独立启动浏览器，依次探测 平凡页(data:) -> 携程 -> 飞猪，
每页做 goto/evaluate/screenshot 三步（全部有界），冻结时打印渲染主线程 wchan。
若 cfg1 冻结而 cfg2 正常 => args 是根因；反之 stealth；两者都冻 => 都有关；cfg3 特有 => 路由/移动上下文。

用法:
    python3 mcp/flight_search/diag_bisect.py
    python3 mcp/flight_search/diag_bisect.py --url <url>   # 只测指定页(所有配置)
"""

import os
import re
import signal
import subprocess
import sys
import time
from typing import Any, TypedDict

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

signal.alarm(900)  # 总看门狗：15 分钟强制退出

CTRIP_URL = "https://m.ctrip.com/html5/flight/swift/index?flightWay=oneway"
FLIGGY_URL = (
    "https://sjipiao.fliggy.com/flight_search_result.htm?tripType=0"
    "&depCity=SHA&arrCity=CAN&depDate=2026-08-20"
    "&depCityName=%E4%B8%8A%E6%B5%B7&arrCityName=%E5%B9%BF%E5%B7%9E"
)
ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--lang=zh-CN",
]
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
window.chrome = { runtime: {} };
"""
BLOCK_RESOURCE_TYPES = {"image", "media", "font"}
BLOCK_SDK_URL_PATTERNS = ("ubt.minh.js", "packages/ubt", "resaresonline", "ubtrms")

class _Cfg(TypedDict):
    name: str
    args: bool
    stealth: bool
    route: bool
    mobile: bool


CONFIGS: list[_Cfg] = [
    {"name": "cfg0_默认", "args": False, "stealth": False, "route": False, "mobile": False},
    {"name": "cfg1_args", "args": True, "stealth": False, "route": False, "mobile": False},
    {"name": "cfg2_stealth", "args": False, "stealth": True, "route": False, "mobile": False},
    {"name": "cfg3_full", "args": True, "stealth": True, "route": True, "mobile": True},
]


def say(tag: str, msg: str) -> None:
    print(f"[bisect][{tag}] {msg}", flush=True)


def fmt_time(t0: float) -> str:
    return f"{time.time() - t0:.1f}s"


def probe(ctx: BrowserContext, browser: Browser, tag: str, url: str, has_route: bool) -> None:
    t0 = time.time()
    say(tag, f"===== 探测 {tag}: {url}")
    page: Page | None = None
    try:
        page = ctx.new_page()

        if has_route:
            def _abort_heavy(route: Any) -> None:
                rurl = route.request.url
                if route.request.resource_type in BLOCK_RESOURCE_TYPES or any(
                    p in rurl for p in BLOCK_SDK_URL_PATTERNS
                ):
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", _abort_heavy)

        t = time.time()
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            say(tag, f"goto 完成: {page.url[:100]} ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"goto 异常: {type(e).__name__}: {str(e)[:120]}")

        t = time.time()
        ok_eval = True
        try:
            title = page.wait_for_function("() => document.title", timeout=8000).json_value()
            say(tag, f"evaluate 可用: title={title!r} ({fmt_time(t)})")
        except Exception as e:
            ok_eval = False
            say(tag, f"!! evaluate 超时: {type(e).__name__} ({fmt_time(t)})")

        t = time.time()
        try:
            page.screenshot(path=f"catch/bisect_{tag}.png", timeout=5000)
            say(tag, f"screenshot 可用 ({fmt_time(t)})")
        except Exception as e:
            say(tag, f"!! screenshot 超时: {type(e).__name__}: {str(e)[:80]}")

        # 冻结诊断：浏览器级 CDP + 渲染主线程 wchan（与生产冻结签名对比）
        if not ok_eval or True:
            try:
                cdp = browser.new_browser_cdp_session()
                info = cdp.send("SystemInfo.getProcessInfo")
                for p in info.get("processInfo", []):
                    if p.get("type") == "renderer":
                        pid = p.get("id", 0)
                        try:
                            out = subprocess.run(
                                ["ps", "-L", "-p", str(pid), "-o", "tid,%cpu,stat,wchan:24", "--sort=-%cpu"],
                                capture_output=True, text=True, timeout=5,
                            ).stdout
                            lines = [l for l in out.splitlines() if l.strip() and not l.startswith("TID")]
                            main = next((l for l in lines if l.strip().split()[0] == str(pid)), None)
                            say(tag, f"  renderer pid={pid} 主线程: {main if main else '未找到'}")
                        except Exception as e:
                            say(tag, f"  /proc 读取失败: {e}")
            except Exception as e:
                say(tag, f"  CDP失败: {type(e).__name__}: {str(e)[:120]}")
    except Exception as e:
        say(tag, f"!! 探测异常: {type(e).__name__}: {str(e)[:150]}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                say(tag, "page.close 失败(渲染进程无响应)")
        say(tag, f"===== 探测结束 {fmt_time(t0)}")


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

    with sync_playwright() as pw:
        for cfg in CONFIGS:
            name = cfg["name"]
            say(name, f"---------- 启动: args={cfg['args']} stealth={cfg['stealth']} route={cfg['route']} mobile={cfg['mobile']}")
            launch_kw: dict[str, Any] = {"headless": True}
            if cfg["args"]:
                launch_kw["args"] = ARGS
            try:
                browser = pw.chromium.launch(**launch_kw)
            except Exception as e:
                say(name, f"!! 浏览器启动失败: {type(e).__name__}: {str(e)[:150]}")
                continue

            ctx_kw: dict[str, Any] = {"viewport": {"width": 1440, "height": 900}}
            if cfg["mobile"]:
                ctx_kw.update(
                    locale="zh-CN", timezone_id="Asia/Shanghai",
                    user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"),
                    is_mobile=True, has_touch=True, viewport={"width": 390, "height": 844},
                )
            else:
                ctx_kw.update(locale="zh-CN", timezone_id="Asia/Shanghai")
            ctx = browser.new_context(**ctx_kw)
            if cfg["stealth"]:
                ctx.add_init_script(STEALTH_JS)

            try:
                p = ctx.new_page()
                p.goto("data:text/html,<title>bisect-local</title><h1>ok</h1>", timeout=15000)
                p.wait_for_function("() => document.title", timeout=5000)
                p.screenshot(path="catch/bisect_local.png", timeout=5000)
                say(name, "平凡页: 全链路可用")
                p.close()
            except Exception as e:
                say(name, f"!! 平凡页失败: {type(e).__name__}: {str(e)[:100]}")

            if url_only:
                probe(ctx, browser, f"{name}_custom", url_only, cfg["route"])
            else:
                if cfg["mobile"]:
                    # 生产路径：携程移动上下文，飞猪桌面上下文
                    desktop_ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                                                       viewport={"width": 1440, "height": 900})
                    if cfg["stealth"]:
                        desktop_ctx.add_init_script(STEALTH_JS)
                    probe(ctx, browser, f"{name}_ctrip", CTRIP_URL, cfg["route"])
                    probe(desktop_ctx, browser, f"{name}_fliggy", FLIGGY_URL, cfg["route"])
                    desktop_ctx.close()
                else:
                    probe(ctx, browser, f"{name}_ctrip", CTRIP_URL, cfg["route"])
                    probe(ctx, browser, f"{name}_fliggy", FLIGGY_URL, cfg["route"])
            ctx.close()
            try:
                browser.close()
            except Exception:
                say(name, "browser.close 失败")
            say(name, "---------- 配置结束")
    say("main", "二分诊断结束")


if __name__ == "__main__":
    main()
