"""
Thinking 事件源 — RSS 订阅轮询 / WebHook HTTP 回调。

- RssEventSource     : 轮询型源。工作线程按配置间隔抓取全部启用订阅源的新条目，
                       未见过条目封装为 RssEvent 投递到 EventBus（去重记录持久化
                       于 db/thinking.db 的 rss_seen 表，重启不重复处理）。
                       配置每次轮询实时读取（修改间隔/增删源无需重启）。
- WebhookEventSource : 事件驱动型源。工作线程注册完即待命；HTTP 请求线程经
                       handle_request() 校验（启用状态 + 可选密钥）后封装为
                       WebhookEvent 投递。POST /api/thinking/webhook 由
                       webhook_routes.py 桥接进来。

解析：仅用标准库（urllib + xml.etree），支持 RSS 2.0 与 Atom 两种格式；
parse_feed() 为无副作用的纯函数，可直接单元测试（见 tests/test_thinking_sources.py）。
"""

import hmac
import json
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from modules.channels.thinking import store as thinking_store
from modules.events.base import RssEvent, WebhookEvent
from modules.events.event_source import EventSource
from modules.utils.logger import log_error, log_info, log_warning

FETCH_TIMEOUT = 15
_POLL_SLICE_SECONDS = 1.0


# ═══════════════════════════════════════════════════════════════
# RSS / Atom 解析（纯函数）
# ═══════════════════════════════════════════════════════════════


def _local_name(tag: str) -> str:
    """去命名空间的元素名（Atom 默认命名空间 → 'feed'/'entry'）。"""
    return tag.rsplit("}", 1)[-1]


def _text(elem: Optional[ET.Element]) -> str:
    """元素全文（含子元素文本）并折叠空白。"""
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def _find_local(root: ET.Element, name: str) -> Optional[ET.Element]:
    for child in root:
        if _local_name(child.tag) == name:
            return child
    return None


def _find_all_local(root: ET.Element, name: str) -> List[ET.Element]:
    return [child for child in root if _local_name(child.tag) == name]


def _parse_rss(root: ET.Element) -> List[Dict[str, str]]:
    """RSS 2.0：<rss><channel><item>（guid 优先作条目 ID）。"""
    channel = _find_local(root, "channel")
    if channel is None:
        return []
    entries: List[Dict[str, str]] = []
    for item in _find_all_local(channel, "item"):
        link = _text(_find_local(item, "link"))
        guid = _text(_find_local(item, "guid"))
        entry_id = guid or link
        if not entry_id:
            continue
        entries.append(
            {
                "id": entry_id,
                "title": _text(_find_local(item, "title")),
                "link": link,
                "summary": _text(_find_local(item, "description")),
            }
        )
    return entries


def _parse_atom(root: ET.Element) -> List[Dict[str, str]]:
    """Atom：<feed><entry>（id 元素；link 取 rel=alternate/无 rel 者优先）。"""
    entries: List[Dict[str, str]] = []
    for entry in _find_all_local(root, "entry"):
        entry_id = _text(_find_local(entry, "id"))
        if not entry_id:
            continue
        link = ""
        for link_el in _find_all_local(entry, "link"):
            rel = link_el.get("rel", "alternate")
            href = (link_el.get("href") or "").strip()
            if href and rel in ("alternate", ""):
                link = href
                break
        summary_el = _find_local(entry, "summary")
        if summary_el is None:
            summary_el = _find_local(entry, "content")
        entries.append(
            {
                "id": entry_id,
                "title": _text(_find_local(entry, "title")),
                "link": link,
                "summary": _text(summary_el),
            }
        )
    return entries


def parse_feed(xml_bytes: bytes) -> List[Dict[str, str]]:
    """解析 RSS 2.0 / Atom feed，返回条目列表（[{id,title,link,summary}]）。

    纯函数、无副作用；格式不支持或 XML 损坏时抛 ValueError（由调用方处理）。
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"XML 解析失败: {e}")
    kind = _local_name(root.tag)
    if kind == "rss":
        return _parse_rss(root)
    if kind == "feed":
        return _parse_atom(root)
    raise ValueError(f"不支持的 feed 格式（根元素: {root.tag}）")


def fetch_feed(url: str) -> bytes:
    """抓取订阅源内容（超时 15s，仅接受 200；HTTP 错误以异常上抛）。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Helix-Thinking/1.0"},
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read()


# ═══════════════════════════════════════════════════════════════
# RssEventSource — 轮询型事件源
# ═══════════════════════════════════════════════════════════════


class RssEventSource(EventSource):
    """RSS 订阅轮询事件源：周期抓取启用订阅源的新条目并 publish。」

    配置（db/thinking.json 的 rss 段）每次轮询实时读取：
    - interval: 轮询间隔秒数（30-86400，越界值由 store 钳制）
    - feeds   : [{url, enabled}]，仅轮询 enabled=True 的源
       去重用 rss_seen 表（feed_url + entry_id），单锁串行轮询天然串行无竞态。
    """

    source_name = "rss"
    thread_name = "rss-poller"

    def _loop(self) -> None:
        """主循环：轮询一次 → 按间隔分片等待（快速响应停止）。"""
        while not self._stop_event.is_set():
            self.poll_once()
            deadline = time.time() + self._current_interval()
            while not self._stop_event.is_set():
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._stop_event.wait(min(_POLL_SLICE_SECONDS, remaining))

    def _current_interval(self) -> int:
        return thinking_store.load_config()["rss"]["interval"]

    # ── 轮询（供测试直接调用）────────────────────────────────────────

    def poll_once(self) -> int:
        """执行一次全量轮询：返回新发布事件数；单源失败不影响其他源。"""
        cfg = thinking_store.load_config()
        published = 0
        for feed in cfg["rss"]["feeds"]:
            if not feed.get("enabled", True):
                continue
            try:
                published += self._poll_feed(feed["url"])
            except Exception as e:  # 单源失败 → 记录继续（绝不拖垮轮询线程）
                log_warning(f"[rss] 轮询失败 {feed['url']}: {e}")
        if published:
            log_info(f"[rss] poll_once published {published} new event(s)")
        return published

    def _poll_feed(self, feed_url: str) -> int:
        body = fetch_feed(feed_url)
        entries = parse_feed(body)
        published = 0
        for entry in entries:
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id or thinking_store.is_entry_seen(feed_url, entry_id):
                continue
            thinking_store.mark_entries_seen([(feed_url, entry_id)])
            self.publish(
                RssEvent(
                    feed_url=feed_url,
                    entry_id=entry_id,
                    entry_title=str(entry.get("title") or "")[:500],
                    entry_link=str(entry.get("link") or "")[:2000],
                    entry_summary=str(entry.get("summary") or "")[:4000],
                )
            )
            published += 1
        return published


# ═══════════════════════════════════════════════════════════════
# WebhookEventSource — 事件驱动型事件源
# ═══════════════════════════════════════════════════════════════


class WebhookEventSource(EventSource):
    """WebHook 回调事件源：POST /api/thinking/webhook → handle_request()。

    工作线程注册完待命（thread_name 常驻），HTTP 请求线程经 handle_request()
    触发 publish —— 与 EventSource 契约一致（见 event_source.py 事件驱动型说明）。

    校验规则（配置实时读取）：
    - webhook.enabled = False  → 404（端点视为不存在）
    - 配置了 webhook.secret 且请求头 X-Webhook-Secret 不一致 → 401
    - 思考内容取 payload.content（字符串）；缺省则序列化整个 payload
    """

    source_name = "webhook"
    thread_name = "webhook-listener"

    def _loop(self) -> None:
        """事件驱动型源：注册完触发器后待命，事件经 handle_request() 触发。"""
        log_info("[webhook] Listener armed — waiting for POST /api/thinking/webhook")
        self._stop_event.wait()

    # ── 请求处理（Flask 请求线程调用）────────────────────────────────

    def handle_request(self, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        """处理一次 WebHook 回调。

        返回含 status 键的结果 dict（路由层据此决定 HTTP 状态码）：
        - 200: {"ok": True, "event_id": ...}
        - 401/403/404: {"ok": False, "error": ...}
        """
        result: Dict[str, Any] = {"ok": False, "status": 200}
        cfg = thinking_store.load_config()
        webhook_cfg = cfg["webhook"]

        if not webhook_cfg["enabled"]:
            result.update({"status": 404, "error": "WebHook endpoint is disabled"})
            return result

        expected = webhook_cfg["secret"]
        if expected:
            provided = str(headers.get("X-Webhook-Secret", "") or "")
            if not hmac.compare_digest(provided, expected):
                result.update({"status": 401, "error": "invalid X-Webhook-Secret"})
                return result

        try:
            content = str(payload.get("content") or "")
        except Exception:
            content = ""
        if not content.strip():
            try:
                content = json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(payload)
        origin = str(payload.get("source") or "").strip()[:64]
        if not origin:
            origin = str(headers.get("User-Agent", "webhook-client"))[:64]

        event = WebhookEvent(origin=origin, content=content, raw=payload)
        self.publish(event)
        result.update({"ok": True, "status": 200, "event_id": event.event_id})
        return result


# ═══════════════════════════════════════════════════════════════
# 进程级单例（与 get_dispatcher()/get_event_bus() 同模式）
# ═══════════════════════════════════════════════════════════════

_rss_source: Optional[RssEventSource] = None
_webhook_source: Optional[WebhookEventSource] = None
_singleton_lock = threading.Lock()


def get_rss_source() -> RssEventSource:
    """获取进程级唯一的 RSS 轮询事件源。"""
    global _rss_source
    with _singleton_lock:
        if _rss_source is None:
            _rss_source = RssEventSource()
        return _rss_source


def get_webhook_source() -> WebhookEventSource:
    """获取进程级唯一的 WebHook 事件源。"""
    global _webhook_source
    with _singleton_lock:
        if _webhook_source is None:
            _webhook_source = WebhookEventSource()
        return _webhook_source


__all__ = [
    "parse_feed",
    "fetch_feed",
    "RssEventSource",
    "WebhookEventSource",
    "get_rss_source",
    "get_webhook_source",
]