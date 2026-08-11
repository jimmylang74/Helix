#!/usr/bin/env python3
"""冻结现场取证器 v2：线程级证据独立于 CDP，任何冻结路径都落盘 + 自动判定。

v1 缺陷（本版修复）：
    - v1 把 ps_snapshot 线程采样放在 CDP Profiler 之后、同一个 try 内；
      CDP 无响应（主线程紧循环）时直接跳过线程采样 → exit=0 却零证据、零落盘。
    - 本版：探测到冻结后【立即】拍一次线程快照，再并行做 8 秒线程采样 + CDP Profiler；
      无论 CDP 是否可用，线程证据必得、必落盘，并给出判定。

用法（宿主上用爬虫同一 venv 跑）：
    source /home/jimmy/venv/bin/activate
    cd ~/code/Helix
    python3 mcp/flight_search/freeze_profiler.py
    # 可选：FP_URL=https://m.ctrip.com/... 覆盖目标页（测试用）

行为：每轮 3 次加载钓冻结，最多 5 轮；钓到即取证并存 {OUTPUT_PATH}。
    退出码：0=已冻结取证，1=连续 5 轮未复现。

判定逻辑（写入 JSON 的"判定"字段）：
    - CDP 采到 JS 自采样（Top 函数）            → JS 死循环，附卡死函数
    - 主线程 State=R / %CPU 高 / wchan 空       → JS 紧循环（CDP 无响应）
    - 主线程 State=S / wchan=poll|sock|read     → 线程阻塞（网络/系统调用）
    - 其余                                     → 无法判定（原始证据已落盘）

注意：async API 单线程使用（sync API 的 greenlet 非线程安全）；
asyncio.wait_for 给每个调用加真超时，全程有界。
"""
import asyncio
import json
import os
import subprocess
import time
from typing import Any

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
window.chrome = { runtime: {} };
"""
_BLOCK_RESOURCE_TYPES = {"image", "media", "font"}
URL = os.environ.get("FP_URL") or "https://m.ctrip.com/html5/flight/swift/index?flightWay=oneway"
MAX_ROUNDS = int(os.environ.get("FP_ROUNDS", "5"))
RENDERER_RE = os.environ.get("FP_RENDERER_RE", "chrome-headless.*--type=renderer")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "freeze_profile.json")


def sh(cmd: str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()[:2000]
    except Exception as e:
        return f"ERROR: {e}"


def fingerprint() -> None:
    print("===== 1. 环境指纹 =====", flush=True)
    print("ulimit -n:", sh("ulimit -n"), flush=True)
    print("/dev/shm:", sh("df -h /dev/shm | tail -1"), flush=True)
    print("/tmp 可用:", sh("df -h /tmp | tail -1"), flush=True)
    print("环境变量:", flush=True)
    for k, v in sorted(os.environ.items()):
        if any(t in k.upper() for t in ("DISPLAY", "XDG", "DBUS", "WAYLAND", "LD_", "EGL", "GL_", "CUDA", "LIBGL", "OLLAMA")):
            print(f"  {k}={v[:120]}", flush=True)
    print("fontconfig:", sh("fc-list 2>/dev/null | wc -l") + " 个字体", flush=True)
    print("chrome 残留进程:", sh("pgrep -a -f '[c]hrome-headless|[c]hromium' | wc -l") + " 个", flush=True)


async def probe(page: Any, label: str) -> bool:
    t0 = time.time()
    try:
        v = await asyncio.wait_for(page.evaluate("() => 1 + 1"), timeout=8)
        print(f"[探针] {label}: {time.time()-t0:.2f}s  值={v}  [页面存活]", flush=True)
        return True
    except asyncio.TimeoutError:
        print(f"[探针] {label}: {time.time()-t0:.2f}s  超时  [主线程冻结!]", flush=True)
        return False
    except Exception as e:
        print(f"[探针] {label}: {time.time()-t0:.2f}s  {type(e).__name__} {str(e)[:60]}", flush=True)
        return False


def ps_snapshot(renderer_pid: str) -> tuple[str, list[dict[str, Any]]]:
    """线程级快照。返回 (可读文本, 结构化线程行[{"tid","cpu","stat","wchan"}])。"""
    lines: list[str] = []
    rows: list[dict[str, Any]] = []
    lines.append("  进程级:")
    lines.append(sh("ps -eo pid,%cpu,stat,comm,args --sort=-%cpu | grep -E '[c]hrome-headless|[c]hromium' | head -6"))
    if renderer_pid.isdigit():
        lines.append(f"  线程级 (pid {renderer_pid} tid %cpu stat wchan):")
        thread_out = sh(f"ps -L -p {renderer_pid} -o tid,%cpu,stat,wchan:28 --sort=-%cpu | head -8")
        lines.append(thread_out)
        for line in thread_out.splitlines()[1:]:  # 跳过 "TID %CPU STAT WCHAN" 表头
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                try:
                    rows.append({"tid": parts[0], "cpu": float(parts[1]), "stat": parts[2], "wchan": parts[3]})
                except ValueError:
                    continue
        lines.append(sh(f"grep -E 'State|voluntary_ctxt|nonvoluntary_ctxt' /proc/{renderer_pid}/status"))
    text = "\n".join(lines)
    print(text, flush=True)
    return text, rows


def top_js_rows(prof: dict[str, Any]) -> list[dict[str, Any]]:
    profile = prof.get("profile", {})
    nodes = profile.get("nodes", [])
    samples = profile.get("samples", [])
    self_hits: dict[int, int] = {}
    for s in samples:
        self_hits[s] = self_hits.get(s, 0) + 1
    rows: list[dict[str, Any]] = []
    for n in nodes:
        nid = n.get("id")
        hits = self_hits.get(nid, 0)
        if hits == 0:
            continue
        cf = n.get("callFrame", {})
        url = cf.get("url", "")
        if not url or url.startswith("chrome") or url.startswith("v8") or "://" not in url:
            url = f"[内部] {url}" if url else "[anonymous]"
        rows.append({
            "samples": hits,
            "ms": hits * 1000,
            "fn": cf.get("functionName", "(anonymous)")[:60],
            "url": url[:100],
            "line": cf.get("lineNumber", -1) + 1,
        })
    rows.sort(key=lambda r: -r["samples"])
    return rows


def classify(thread_rows: list[dict[str, Any]], top_js: list[dict[str, Any]], cdp_ok: bool, renderer_pid: str) -> tuple[str, str]:
    """给出判定。优先 JS 自采样；否则看主线程(tid==pid) state/wchan/%cpu。"""
    if top_js:
        t = top_js[0]
        return "JS死循环", f"Top函数 {t['fn']} @ {t['url']}:{t['line']}（{t['samples']} 样本/{t['ms']}ms）"

    if thread_rows:
        busy = thread_rows[0]  # ps -L 已按 %cpu 降序，首行即最忙线程
    else:
        return "无法判定", "未捕获到 renderer 线程数据"
    cpu, stat, wchan = busy["cpu"], busy["stat"], busy["wchan"]
    if stat.startswith("R") and cpu > 10 and wchan == "-":
        return "JS紧循环(CDP无响应)", f"线程 {busy['tid']} State={stat} %CPU={cpu:.0f}% wchan=空 → 忙等"
    if stat.startswith("S") and wchan not in ("-", ""):
        return "线程阻塞(网络/系统调用)", f"线程 {busy['tid']} State={stat} %CPU={cpu:.0f}% wchan={wchan}"
    return "无法判定", f"线程 {busy['tid']} State={stat} %CPU={cpu:.0f}% wchan={wchan or '-'}"


async def sample_threads(renderer_pid: str, n: int = 4) -> tuple[list[str], list[dict[str, Any]]]:
    snaps: list[str] = []
    rows: list[dict[str, Any]] = []
    for i in range(n):
        await asyncio.sleep(2)
        print(f"  --- 线程采样点 {i+1}/{n} (renderer PID {renderer_pid}) ---", flush=True)
        text, row = await asyncio.to_thread(ps_snapshot, renderer_pid)
        snaps.append(text)
        rows.extend(row)
    return snaps, rows


async def sample_cdp(ctx: Any, page: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    """CDP Profiler 采样（全程有界）。返回 (top_js_rows, error)。"""
    try:
        cdp = await asyncio.wait_for(ctx.new_cdp_session(page), timeout=10)
        await asyncio.wait_for(cdp.send("Profiler.enable"), timeout=10)
        await asyncio.wait_for(cdp.send("Profiler.start", {"samplingInterval": 1000}), timeout=10)
        for _ in range(4):
            await asyncio.sleep(2)
        prof = await asyncio.wait_for(cdp.send("Profiler.stop"), timeout=15)
    except asyncio.TimeoutError:
        return None, "CDP 无响应(超时)"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:100]}"
    return top_js_rows(prof), None


async def run_one_round(round_no: int) -> bool:
    """单轮取证：启动浏览器 + 3 次钓冻结。True=已冻结取证，False=未复现。"""
    from playwright.async_api import async_playwright

    print(f"===== 第 {round_no} 轮：启动 chromium 并加载 {URL[:60]} =====", flush=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--lang=zh-CN",
            ],
        )
        print(f"浏览器: {b.version}  可执行: {p.chromium.executable_path}", flush=True)
        ctx = await b.new_context(
            locale="zh-CN", timezone_id="Asia/Shanghai", user_agent=MOBILE_UA,
            is_mobile=True, has_touch=True, viewport={"width": 390, "height": 844},
        )
        await ctx.add_init_script(_STEALTH_JS)
        page = await ctx.new_page()

        async def abort_heavy(route: Any) -> None:
            if route.request.resource_type in _BLOCK_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", abort_heavy)

        frozen = False
        for attempt in (1, 2, 3):
            print(f"--- 加载尝试 {attempt}/3 ---", flush=True)
            try:
                await asyncio.wait_for(
                    page.goto(URL, wait_until="domcontentloaded"), timeout=45
                )
                print(f"goto 完成: {page.url[:90]}", flush=True)
            except asyncio.TimeoutError:
                print("!! goto 超时 45s（加载阶段即冻结，立即取证）", flush=True)
                frozen = True
                break
            await page.wait_for_timeout(2000)
            alive = await probe(page, f"尝试{attempt}加载后")
            if alive:
                continue
            frozen = True
            break

        if not frozen:
            print(f"3 次加载均健康（页面存活）——第 {round_no} 轮未复现冻结", flush=True)
            await b.close()
            return False

        renderer_pid = await asyncio.to_thread(
            sh, f"pgrep -f '{RENDERER_RE}' | head -1"
        )
        print(f"renderer PID: {renderer_pid}", flush=True)

        print("===== 3. 立即快照 + 并行采样 8 秒（线程级 + CDP Profiler）=====", flush=True)
        t0_text, t0_rows = await asyncio.to_thread(ps_snapshot, renderer_pid)
        thread_task = asyncio.create_task(sample_threads(renderer_pid))
        cdp_task = asyncio.create_task(sample_cdp(ctx, page))
        snap_texts, snap_rows = await thread_task
        thread_texts = [t0_text] + snap_texts
        thread_rows = t0_rows + snap_rows
        top_js, cdp_err = await cdp_task
        top_js = top_js or []  # CDP 无响应时 top_js 为 None，后续一律按 [] 处理
        if cdp_err:
            print(f"!! CDP Profiler 未采到: {cdp_err}——以线程级证据为准", flush=True)
        else:
            print("CDP Profiler 采样成功", flush=True)

        # 原始证据【先落盘】，再打印/判定——任何后续异常都不丢证据
        summary = {
            "判定": "未判定",
            "依据": "",
            "cdp_profiler": "成功" if cdp_err is None else f"失败({cdp_err})",
            "renderer_pid": renderer_pid,
            "top_js": top_js[:12],
            "线程快照": thread_texts,
        }
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"原始证据已落盘 {OUTPUT_PATH}", flush=True)

        await probe(page, "采样后")
        await b.close()

        print("===== 4. 占用 CPU 最多的 JS 函数（Top 12）=====", flush=True)
        for r in top_js[:12]:
            print(f"  {r['samples']:5d} 样本 ~{r['ms']}ms  {r['fn']}  {r['url']}:{r['line']}", flush=True)
        if not top_js:
            print("  （无 JS 自采样——主线程可能在系统调用/等待中，而非 JS 循环）", flush=True)

        verdict, detail = classify(thread_rows, top_js, cdp_err is None, renderer_pid)
        summary["判定"] = verdict
        summary["依据"] = detail
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"===== 5. 判定: {verdict} =====", flush=True)
        print(f"     依据: {detail}", flush=True)
        print(f"[第 {round_no} 轮] 冻结已取证，完整取证已存 {OUTPUT_PATH}", flush=True)
        return True


async def run() -> None:
    for round_no in range(1, MAX_ROUNDS + 1):
        try:
            caught = await run_one_round(round_no)
        except Exception as e:
            print(f"!! 第 {round_no} 轮取证器自身异常（跳过继续）: {type(e).__name__}: {e}", flush=True)
            continue
        if caught:
            return
    print(
        f"连续 {MAX_ROUNDS} 轮均未复现冻结——冻结为间歇性，本轮未取证；"
        "可稍后重跑或直接重试完整搜索",
        flush=True,
    )
    raise SystemExit(1)


if __name__ == "__main__":
    fingerprint()
    asyncio.run(run())
