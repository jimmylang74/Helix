"""Playwright 浏览器会话管理。

每个爬虫调用创建独立的浏览器会话（线程安全：同步 API 实例互相隔离），
请求结束统一关闭，避免泄漏。统一设置中文环境与常见反爬规避参数。

支持两种模式：
    - 桌面模式（默认）：Windows Chrome UA，1440x900，用于飞猪等桌面站点
    - 移动模式（mobile=True）：iPhone Safari UA + 触屏视口，用于携程移动版 H5
"""

import os
import time
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

# 常见 Chrome UA（Windows Chrome 126）
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# iPhone Safari UA（携程移动版 H5 需要移动 UA + 触屏环境）
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)

# 隐藏自动化特征：webdriver / plugins / languages / chrome 对象
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
window.chrome = { runtime: {} };
"""

# 跳过静态资源，加速页面加载（不影响 JS 渲染与数据提取）
_BLOCK_RESOURCE_TYPES = {"image", "media", "font"}

# 风控/埋点 SDK：会发起同步 XHR 上报风险指纹（发完才放行后续操作）。
# 实测该请求挂起时渲染主线程阻塞在 futex 等待（wchan=futex_wait_queue，零上下文切换）
# 导致整页冻结（evaluate/click/screenshot/CDP 全部超时）。直接 abort 使其快速失败，
# 从源头消除冻结（爬虫场景无需也不应触发风控上报）。
_BLOCK_SDK_URL_PATTERNS = (
    "ubt.minh.js",   # 携程埋点 SDK（static.tripcdn.com/packages/ubt/...）
    "packages/ubt",  # 埋点资源目录
    "resaresonline", # 携程风控资源域（webresource.c-ctrip.com/resaresonline/risk/...）
    "ubtrms",        # 携程风控 SDK
)

# 首次访问常见弹层按钮文本（DOM 覆盖层，非原生对话框）：
# 风险提示/协议弹窗 + cookie 同意横幅（中英文）
_POPUP_BUTTON_TEXTS = (
    "风险提示", "我知道了", "知道了", "同意并继续", "同意", "确认", "确定",
    "接受全部", "全部接受", "接受所有", "允许所有", "接受", "允许",
    "Accept All", "ACCEPT ALL", "Accept", "ACCEPT", "Got it", "OK",
)


def evaluate_bounded(
    page: Page,
    js: str,
    timeout_s: float,
    tag: str,
    shot_path: str | None = None,
) -> Any:
    """对页面脚本执行施加驱动端限时。

    Playwright sync API 的 evaluate 无超时参数（实测可无限等待，set_default_timeout
    也无效）；wait_for_function 的计时在驱动端，主线程被占也会按时抛 TimeoutError。
    用它执行提取脚本：脚本返回数组等 truthy 值即返回；超时截图留证后抛错。
    """
    try:
        return page.wait_for_function(js, timeout=int(timeout_s * 1000)).json_value()
    except Exception:
        if shot_path:
            try:
                page.screenshot(path=shot_path, timeout=5000)
                print(f"[flight][{tag}] 脚本执行超时，已截图 {shot_path}", flush=True)
            except Exception as exc:
                print(f"[flight][{tag}] 超时后截图失败: {type(exc).__name__}: {str(exc)[:80]}", flush=True)
        raise


def dismiss_dom_popup(page: Page, tag: str) -> None:
    """点击常见弹层按钮关闭 DOM 覆盖层（首次访问的风险提示/协议弹窗）。"""
    for text in _POPUP_BUTTON_TEXTS:
        try:
            page.get_by_text(text, exact=True).first.click(timeout=1200)
            page.wait_for_timeout(800)
            print(f"[flight][{tag}] 已关闭弹层: {text}", flush=True)
            return
        except Exception:
            continue


class BrowserSession:
    """一次性 Playwright 会话：浏览器 + 上下文，用完后 close()。"""

    def __init__(self, headless: bool = True, mobile: bool = False) -> None:
        try:
            self._pw: Playwright = sync_playwright().start()
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                f"Playwright 启动失败: {e}（请先执行: python3 -m playwright install chromium）"
            ) from e
        self.browser: Browser = self._pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--lang=zh-CN",
            ],
        )
        self.user_agent: str = MOBILE_UA if mobile else CHROME_UA
        kw: dict[str, Any] = dict(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=self.user_agent,
        )
        if mobile:
            kw.update(is_mobile=True, has_touch=True, viewport={"width": 390, "height": 844})
        else:
            kw["viewport"] = {"width": 1440, "height": 900}
        self.context: BrowserContext = self.browser.new_context(**kw)
        self.context.add_init_script(_STEALTH_JS)

    def new_page(self) -> Page:
        page = self.context.new_page()
        # 自动关闭原生 JS 对话框（alert/confirm/prompt）：携程移动版在特定
        # IP/UA 下会弹"请在浏览器中打开"等原生提示，不处理会导致 evaluate/click 无限挂起
        def _dismiss_dialog(dialog: Any) -> None:
            print(f"[flight][browser] !! 捕获到原生对话框: {dialog.type} | {str(dialog.message)[:80]}", flush=True)
            dialog.dismiss()

        page.on("dialog", _dismiss_dialog)

        def _abort_heavy(route: Any) -> None:
            url = route.request.url
            if route.request.resource_type in _BLOCK_RESOURCE_TYPES or any(
                p in url for p in _BLOCK_SDK_URL_PATTERNS
            ):
                route.abort()
            else:
                route.continue_()

        page.route("**/*", _abort_heavy)
        return page

    def dump_page_html(self, page: Page, source: str, tag: str) -> None:
        """把当前页面 HTML 保存到 ./catch/ 目录，供离线定位页面状态。

        主线程冻结（evaluate/screenshot 均超时）时走网络层重放：
        context.request 在驱动端执行、自带超时、自动共享页面 cookie，
        不依赖渲染进程，冻结页上也能拿到服务器原始响应。所有操作有界。

        保存文件（相对 server 启动目录）：
            catch/{source}_{tag}_{时间戳}_rendered.html  渲染后 DOM（页面存活时）
            catch/{source}_{tag}_{时间戳}_raw.html       网络层原始 HTML
        """
        os.makedirs("catch", exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        saved: list[str] = []

        # 1) 渲染后 DOM：页面存活时最有诊断价值（能看到风控提示/实际渲染结果）
        try:
            html = evaluate_bounded(
                page, "() => document.documentElement.outerHTML", 8, source
            )
            if isinstance(html, str) and html.strip():
                path = os.path.join("catch", f"{source}_{tag}_{stamp}_rendered.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
                saved.append(path)
        except Exception as exc:
            print(
                f"[flight][{source}] 渲染DOM获取失败(页面可能冻结): "
                f"{type(exc).__name__}: {str(exc)[:80]}",
                flush=True,
            )

        # 2) 网络层重放：冻结时也能拿到服务器响应，cookie 自动共享
        if page.url.startswith("http"):
            try:
                resp = self.context.request.get(
                    page.url,
                    headers={"User-Agent": self.user_agent},
                    timeout=20000,
                )
                body_bytes = resp.body()
                try:
                    body = body_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    body = body_bytes.decode("gb18030", errors="replace")
                if len(body) > 3_000_000:
                    body = f"<!-- 原始 {len(body)} 字节，已截断 -->\n" + body[:3_000_000]
                path = os.path.join("catch", f"{source}_{tag}_{stamp}_raw.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
                saved.append(path)
            except Exception as exc:
                print(
                    f"[flight][{source}] 网络层重放失败: {type(exc).__name__}: {str(exc)[:80]}",
                    flush=True,
                )
        else:
            print(f"[flight][{source}] 页面 URL 非 http，跳过网络重放: {page.url[:80]}", flush=True)

        if saved:
            print(f"[flight][{source}] 页面已保存(便于定位): {', '.join(saved)}", flush=True)
        else:
            print(f"[flight][{source}] !! 页面保存失败，无任何落盘文件", flush=True)

    def close(self) -> None:
        try:
            self.browser.close()
        finally:
            self._pw.stop()
