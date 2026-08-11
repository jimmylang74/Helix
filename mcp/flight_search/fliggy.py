"""飞猪（Fliggy）机票爬虫 — 结果页 URL 直接访问。

飞猪搜索首页（flights.fliggy.com）走表单交互会打开多个标签页且易被风控；
但结果页 URL（sjipiao.fliggy.com/flight_search_result.htm）可匿名直接访问，
参数为三字码 + 中文城市名，返回按价格升序的航班列表（已去零价/中转）。

结果卡片选择器稳定为 .J_FlightItem（= .flight-list-item），
卡片文本以"航司+航班号"开头（如"吉祥HO1253"），航班号取最左匹配；
伪造航班号（机型 C919/A320 等）与 T2xx 航站楼噪声一并过滤。
"""

import re
from typing import Any
from urllib.parse import quote

from .browser import BrowserSession, dismiss_dom_popup, evaluate_bounded
from .cities import to_city_name, to_iata

# 飞猪机票搜索结果页（桌面 UA 可匿名访问）
FLIGGY_RESULT_URL = "https://sjipiao.fliggy.com/flight_search_result.htm"

# 跳转到淘宝登录 / 出现登录墙提示视为被风控
_LOGIN_WALL_HINTS = ("登录后", "扫码登录", "请登录", "手机验证", "拖动滑块", "滑块验证")


def _looks_blocked(page: Any) -> bool:
    """通过页面文本判断是否被反爬/登录墙拦截。"""
    try:
        body = page.inner_text("body", timeout=5000)
    except Exception:
        return False
    return any(hint in body for hint in _LOGIN_WALL_HINTS)


# 提取 JS（probe13 验证：113 条，odd:0）：
#   - 航班号取"最左"匹配（文本以"吉祥HO1253"式航司+航班号开头）
#   - 过滤伪造航班号：航站楼 T2+数字、机型 A320/B737/C919
_FLIGGY_EXTRACT_JS = r"""() => {
  const flightNoRe = /[A-Z0-9]{2}[0-9]{2,5}/g;
  const timeRe = /(\d{1,2}:\d{2})/g;
  const priceRe = /¥\s*(\d{2,6})/;
  const isFake = (f) => /^T\d{2,5}$/.test(f) || /^(A\d{3}|B\d{3}|C9\d{2})$/.test(f);

  const parseCard = (card) => {
    const text = card.textContent || '';
    const allNo = (text.match(flightNoRe) || []).filter(f => !isFake(f));
    const fno = allNo.length ? allNo[0] : null;  // 最左：航司+航班号在文本开头
    const times = text.match(timeRe) || [];
    const pm = text.match(priceRe);
    if (!fno || !pm || times.length < 2) return null;
    return { flight: fno, depTime: times[0], arrTime: times[1], price: parseInt(pm[1], 10) };
  };

  const results = new Map();
  const sels = ['.J_FlightItem', '.flight-list-item', '[class*="flight-item"]', '[class*="flightItem"]'];
  for (const sel of sels) {
    for (const card of document.querySelectorAll(sel)) {
      const r = parseCard(card);
      if (r && r.price > 0) results.set(r.flight + '|' + r.depTime, r);
    }
    if (results.size > 0) break;
  }
  return Array.from(results.values());
}
"""


def search_fliggy(
    dep_city: str,
    arr_city: str,
    date: str,
    max_results: int = 20,
    page_timeout_ms: int = 60000,
) -> tuple[list[dict[str, Any]], str | None]:
    """在飞猪搜索航班（直接访问结果 URL）。

    Args:
        dep_city/arr_city: 城市中文名（如"上海"）或三字码（如"SHA"）
        date: 出发日期 YYYY-MM-DD

    Returns:
        (航班列表, 错误信息)，语义同 search_ctrip。
    """
    try:
        dep_iata = to_iata(dep_city)
        arr_iata = to_iata(arr_city)
    except ValueError as e:
        return [], f"飞猪参数错误: {e}"

    dep_name = to_city_name(dep_city)
    arr_name = to_city_name(arr_city)
    url = (
        f"{FLIGGY_RESULT_URL}?tripType=0&depCity={dep_iata}&arrCity={arr_iata}"
        f"&depDate={date}&depCityName={quote(dep_name)}&arrCityName={quote(arr_name)}"
    )

    session = BrowserSession()  # 桌面 UA
    page: Any = None
    try:
        page = session.new_page()
        print(f"[flight][fliggy] 直接访问结果 URL: {url[:160]}", flush=True)
        page.goto(url, wait_until="domcontentloaded", timeout=page_timeout_ms)
        print(f"[flight][fliggy] goto 完成，当前URL: {page.url[:150]}", flush=True)
        dismiss_dom_popup(page, "fliggy")
        try:
            page.wait_for_selector(".J_FlightItem", timeout=20000)
            print(f"[flight][fliggy] 结果卡片已出现", flush=True)
        except Exception:
            print(f"[flight][fliggy] 20s 内未等到 .J_FlightItem，当前URL: {page.url[:150]}", flush=True)
            session.dump_page_html(page, "fliggy", "result")
        page.wait_for_timeout(1500)
        print(f"[flight][fliggy] 等待 1.5s 完成，进入检查", flush=True)

        if "taobao" in page.url:
            print(f"[flight][fliggy] !! 被跳转到淘宝（风控）: {page.url[:120]}", flush=True)
            session.dump_page_html(page, "fliggy", "blocked")
            return [], "飞猪被风控跳转淘宝（需登录，无法匿名获取价格）"
        if _looks_blocked(page):
            print(f"[flight][fliggy] !! 页面出现登录/滑块验证提示", flush=True)
            session.dump_page_html(page, "fliggy", "blocked")
            return [], "飞猪被反爬拦截（页面出现登录/滑块验证提示）"
        print(f"[flight][fliggy] 风控检查通过，开始提取", flush=True)

        raw: list[Any] = []
        for attempt in (1, 2):
            try:
                raw = evaluate_bounded(page, _FLIGGY_EXTRACT_JS, 15, "fliggy", "/tmp/fliggy_result_debug.png")
                break
            except Exception as exc:
                print(f"[flight][fliggy] !! 提取尝试 {attempt} 失败: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                if attempt == 1:
                    page.wait_for_timeout(5000)
                    continue
                session.dump_page_html(page, "fliggy", "result")
                return [], f"飞猪结果页提取失败: {exc}"
        print(f"[flight][fliggy] evaluate 完成，提取到 {len(raw)} 条候选航班", flush=True)
        flights: list[dict[str, Any]] = []
        for f in raw[:max_results]:
            flights.append({
                "航班号": f["flight"],
                "起飞时间": f["depTime"],
                "出发城市": dep_name,
                "目的城市": arr_name,
                "价格": f["price"],
                "来源": "飞猪",
            })
        if not flights:
            print(f"[flight][fliggy] 提取为空，无结果", flush=True)
            return [], "飞猪未找到航班（可能该航线/日期无结果，或页面加载超时）"
        print(f"[flight][fliggy] 返回 {len(flights)} 条航班，最低价 ¥{min(f['价格'] for f in flights)}", flush=True)
        return flights, None
    except Exception as e:
        print(f"[flight][fliggy] !! 异常: {type(e).__name__}: {e}", flush=True)
        if page is not None:
            session.dump_page_html(page, "fliggy", "fail")
        return [], f"飞猪抓取失败: {e}"
    finally:
        session.close()
        print(f"[flight][fliggy] 浏览器会话已关闭", flush=True)
