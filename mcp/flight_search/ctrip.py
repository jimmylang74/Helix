"""携程（Ctrip）机票爬虫 — 移动版 H5 表单交互流程。

携程桌面版（flights.ctrip.com）有 whaleguard 反爬，无法匿名访问；
移动版 m.ctrip.com 可匿名访问，但结果页 URL 直接访问会跳转登录，
必须通过首页表单交互（选城市/日期 → 点搜索）到达结果页。

页面为 Taro 框架，稳定选择器是 data-testid：
    u_departure_city / u_arrival_city / u_departure_date / u_btn_search
    城市弹层搜索框 .f-taro-input_main，结果项 [class*="father-container"]
    日历日期 [data-testid="date-item-YYYY-MM-DD"]
结果卡片为 [class*="FlightListCard_CardContentContainer"]。
"""

import re
from typing import Any

from .browser import BrowserSession, dismiss_dom_popup, evaluate_bounded

# 携程移动版首页（单程）
CTRIP_INDEX_URL = "https://m.ctrip.com/html5/flight/swift/index?flightWay=oneway"

# 页面出现这些文本视为被反爬拦截
_BLOCK_HINTS = (
    "验证", "安全验证", "人机验证", "请输入验证码", "访问异常",
    "异常访问", "请完成验证", "captcha", "verify",
)

# 提取 JS：
#   - 航班号取"最右"非伪造匹配（卡片文本为"时间 机场 价格 航司航班号"，真实航班号靠后）
#   - 过滤伪造航班号：航站楼 T2+数字（T220/T216）、机型 A320/B737/C919
#   - 时间正则不用词边界（"T216:15" 里的 16:15 需要能匹配）
#   - 价格锚点兜底接受纯数字叶子（价格 DOM 常把 ¥ 与数字拆成两个 span）
_EXTRACT_JS = r"""() => {
  const flightNoRe = /[A-Z0-9]{2}[0-9]{2,5}/g;
  const timeRe = /(\d{1,2}:\d{2})/g;
  const priceRe = /¥\s*(\d{2,6})/;
  const isFake = (f) => /^T\d{2,5}$/.test(f) || /^(A\d{3}|B\d{3}|C9\d{2})$/.test(f);

  const parseCard = (card) => {
    const text = card.textContent || '';
    const allNo = (text.match(flightNoRe) || []).filter(f => !isFake(f));
    const fno = allNo.length ? allNo[allNo.length - 1] : null;
    const times = text.match(timeRe) || [];
    const pm = text.match(priceRe);
    if (!fno || !pm || times.length < 2) return null;
    return { flight: fno, depTime: times[0], arrTime: times[1], price: parseInt(pm[1], 10) };
  };

  const results = new Map();
  const cardSelectors = [
    '[class*="FlightListCard_CardContentContainer"]',
    '.flight-item', '[class*="flight-item"]', '[class*="flightItem"]',
    '[class*="flight-card"]', '[class*="flightCard"]',
  ];
  for (const sel of cardSelectors) {
    for (const card of document.querySelectorAll(sel)) {
      const r = parseCard(card);
      if (r && r.price > 0) results.set(r.flight + '|' + r.depTime, r);
    }
    if (results.size > 0) break;
  }

  // 兜底：价格锚点向上回溯（DOM 结构变化时仍可工作）
  if (results.size === 0) {
    const leaves = document.querySelectorAll('div,span');
    for (const el of leaves) {
      if (el.children.length !== 0) continue;
      const own = (el.textContent || '').trim();
      if (!(/^¥\s*\d{2,6}$/.test(own) || /^\d{2,6}$/.test(own))) continue;
      let node = el;
      for (let i = 0; i < 7 && node; i++) {
        node = node.parentElement;
        if (!node) continue;
        const text = node.textContent || '';
        const allNo = (text.match(flightNoRe) || []).filter(f => !isFake(f));
        const fno = allNo.length ? allNo[allNo.length - 1] : null;
        const times = text.match(timeRe) || [];
        const pm = text.match(priceRe);
        if (fno && times.length >= 2 && pm) {
          results.set(fno + '|' + times[0],
            { flight: fno, depTime: times[0], arrTime: times[1], price: parseInt(pm[1], 10) });
          break;
        }
      }
    }
  }

  return Array.from(results.values());
}
"""


def _looks_blocked(page: Any) -> bool:
    """通过页面文本判断是否被反爬拦截。"""
    try:
        body = page.inner_text("body", timeout=5000)
    except Exception:
        return False
    return any(hint in body for hint in _BLOCK_HINTS)


def _dismiss_popup(page: Any) -> None:
    """关闭首页可能出现的活动弹窗（不影响流程则跳过）。"""
    for text in ("知道了", "关闭"):
        try:
            page.get_by_text(text, exact=True).first.click(timeout=1500)
            page.wait_for_timeout(1000)
            return
        except Exception:
            continue


def _pick_city(page: Any, testid: str, city: str) -> None:
    """在城市弹层中选择城市。testid: u_departure_city | u_arrival_city。"""
    print(f"[flight][ctrip] 选择城市 {city} (testid={testid})", flush=True)
    page.click(f'[data-testid="{testid}"]', timeout=8000)
    page.wait_for_timeout(2500)
    print(f"[flight][ctrip]   弹层已打开，输入搜索词", flush=True)
    page.fill(".f-taro-input_main input, .f-taro-input_main", city, timeout=5000)
    page.wait_for_timeout(1500)
    print(f"[flight][ctrip]   点击联想结果", flush=True)
    page.click(f'[class*="father-container"]:has-text("{city}")', timeout=8000)
    page.wait_for_timeout(2000)
    print(f"[flight][ctrip]   城市 {city} 选择完成", flush=True)


def _pick_date(page: Any, date: str) -> None:
    """在日历中选择出发日期。date: YYYY-MM-DD。"""
    print(f"[flight][ctrip] 选择日期 {date}", flush=True)
    page.click('[data-testid="u_departure_date"]', timeout=8000)
    page.wait_for_timeout(2000)
    print(f"[flight][ctrip]   日历已打开，点击日期项", flush=True)
    try:
        page.click(f'[data-testid="date-item-{date}"]', timeout=8000)
        page.wait_for_timeout(1500)
        print(f"[flight][ctrip]   日期 {date} 选择完成", flush=True)
    except Exception as e:
        raise RuntimeError(
            f"未在日历中找到日期 {date}（日历仅显示当月附近日期，请确认日期为未来日期）"
        ) from e


def search_ctrip(
    dep_iata: str,
    arr_iata: str,
    date: str,
    dep_city: str,
    arr_city: str,
    max_results: int = 20,
    page_timeout_ms: int = 60000,
) -> tuple[list[dict[str, Any]], str | None]:
    """在携程搜索航班。

    Args:
        dep_iata/arr_iata: 出发/目的城市三字码（仅用于结果 URL 记录，实际走表单交互）
        date: 出发日期 YYYY-MM-DD
        dep_city/arr_city: 城市中文名（弹层搜索与输出使用）

    Returns:
        (航班列表, 错误信息)。航班为空且被拦截时错误信息非空；
        页面正常但无结果时错误信息为 None 且列表为空。
    """
    session = BrowserSession(mobile=True)
    page: Any = None
    try:
        page = session.new_page()
        navs: list[str] = []
        page.on("framenavigated", lambda f: navs.append(f.url[:100]))
        print(f"[flight][ctrip] 打开携程移动首页 {CTRIP_INDEX_URL}", flush=True)
        page.goto(CTRIP_INDEX_URL, wait_until="domcontentloaded", timeout=page_timeout_ms)
        page.wait_for_timeout(5000)
        print(f"[flight][ctrip] 首页加载完成，当前URL: {page.url[:120]}", flush=True)
        if navs:
            print(f"[flight][ctrip] 导航历史: {navs}", flush=True)
        try:
            diag = evaluate_bounded(
                page,
                """() => {
                    const q = (s) => !!document.querySelector(s);
                    return {
                        hasDep: q('[data-testid="u_departure_city"]'),
                        hasArr: q('[data-testid="u_arrival_city"]'),
                        hasDate: q('[data-testid="u_departure_date"]'),
                        hasBtn: q('[data-testid="u_btn_search"]'),
                        title: document.title,
                        bodyLen: document.body ? (document.body.innerText || '').length : -1,
                        bodyHead: document.body ? (document.body.innerText || '').slice(0, 200) : '',
                    };
                }""",
                10,
                "ctrip",
                "/tmp/ctrip_home_debug.png",
            )
            print(f"[flight][ctrip] 首页诊断: {diag}", flush=True)
        except Exception as exc:
            print(f"[flight][ctrip] !! 首页诊断 evaluate 失败: {type(exc).__name__}: {str(exc)[:150]}", flush=True)
            session.dump_page_html(page, "ctrip", "home")
        _dismiss_popup(page)
        dismiss_dom_popup(page, "ctrip")

        _pick_city(page, "u_departure_city", dep_city)
        _pick_city(page, "u_arrival_city", arr_city)
        _pick_date(page, date)
        print(f"[flight][ctrip] 点击搜索按钮", flush=True)
        page.click('[data-testid="u_btn_search"]', timeout=8000)

        # 等待结果卡片或拦截提示
        print(f"[flight][ctrip] 等待结果卡片 (30s)...", flush=True)
        try:
            page.wait_for_selector(
                '[class*="FlightListCard_CardContentContainer"]', timeout=30000
            )
            print(f"[flight][ctrip] 结果卡片已出现", flush=True)
        except Exception:
            print(f"[flight][ctrip] 30s 内未等到结果卡片，当前URL: {page.url[:150]}", flush=True)
        page.wait_for_timeout(2000)

        if _looks_blocked(page):
            print(f"[flight][ctrip] !! 页面出现拦截提示（验证/风控）", flush=True)
            session.dump_page_html(page, "ctrip", "blocked")
            return [], "携程被反爬拦截（页面出现验证提示）"

        raw: list[Any] = []
        for attempt in (1, 2):
            try:
                raw = evaluate_bounded(page, _EXTRACT_JS, 15, "ctrip", "/tmp/ctrip_result_debug.png")
                break
            except Exception as exc:
                print(f"[flight][ctrip] !! 提取尝试 {attempt} 失败: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                if attempt == 1:
                    page.wait_for_timeout(5000)
                    continue
                session.dump_page_html(page, "ctrip", "result")
                return [], f"携程结果页提取失败: {exc}"
        print(f"[flight][ctrip] 提取到 {len(raw)} 条候选航班", flush=True)
        flights: list[dict[str, Any]] = []
        for f in raw[:max_results]:
            flights.append({
                "航班号": f["flight"],
                "起飞时间": f["depTime"],
                "出发城市": dep_city,
                "目的城市": arr_city,
                "价格": f["price"],
                "来源": "携程",
            })
        if not flights:
            print(f"[flight][ctrip] 提取为空，无结果", flush=True)
            return [], "携程未找到航班（可能该航线/日期无结果，或页面加载超时）"
        print(f"[flight][ctrip] 返回 {len(flights)} 条航班，最低价 ¥{min(f['价格'] for f in flights)}", flush=True)
        return flights, None
    except Exception as e:
        print(f"[flight][ctrip] !! 异常: {type(e).__name__}: {e}", flush=True)
        if page is not None:
            session.dump_page_html(page, "ctrip", "fail")
        return [], f"携程抓取失败: {e}"
    finally:
        session.close()
        print(f"[flight][ctrip] 浏览器会话已关闭", flush=True)
