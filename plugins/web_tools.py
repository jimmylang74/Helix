"""
Web Tools Plugin - Web search and batch URL fetching.
"""

import json
import re
import requests
from typing import Any, Dict, List

from modules.agents.tool_base import BaseTool
from modules.config.config_manager import ConfigManager
from modules.mcp.mcp_registry import registry as mcp_registry
from modules.utils.logger import log_tool_call, log_agent_action, log_error, log_warning


class WebSearchTool(BaseTool):
    """Search the web via MCP (SearXNG) with direct fallback."""

    name = "web_search"
    description = "Search the web for information. Returns a list of URLs with titles and snippets."
    category = "web"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string"
            }
        },
        "required": ["query"]
    }

    def execute(self, query: str = "", **kwargs) -> List[Dict[str, str]]:
        log_tool_call(f"web_search(query='{query}') via MCP")
        try:
            result_text = mcp_registry.call_tool("web_search", {"query": query})
            if result_text:
                results = json.loads(result_text)
                if isinstance(results, list):
                    return results
            return []
        except Exception:
            log_warning("MCP web_search unavailable, using direct fallback")
            return self._fallback(query)

    def _fallback(self, query: str, max_results: int = 10) -> List[Dict[str, str]]:
        config = ConfigManager()
        base_url = config.get("tools.searxng.base_url", "http://localhost:8888")
        try:
            resp = requests.post(
                f"{base_url}/search",
                data={
                    "q": query, "format": "json", "language": "zh-CN",
                    "categories": "general", "pageno": 1,
                },
                timeout=15,
                headers={"Accept": "application/json"}
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("results", [])[:max_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "content": item.get("content", ""),
                    })
                return results
        except Exception:
            pass
        return [{
            "title": f"Mock Result: {query} - Overview",
            "url": f"https://example.com/result?q={query.replace(' ', '+')}",
            "content": f"Mock search result for '{query}'. Configure MCP SearXNG server for real results."
        }]


class WebFetchBatchTool(BaseTool):
    """Fetch content from multiple URLs and combine."""

    name = "web_fetch_batch"
    description = "Fetch and extract text content from multiple URLs. Returns combined text."
    category = "web"
    parameters = {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of URLs to fetch"
            }
        },
        "required": ["urls"]
    }

    def execute(self, urls: List[str] = None, **kwargs) -> str:
        urls = urls or []
        log_tool_call(f"web_fetch_batch({len(urls)} URLs)")
        combined = []
        for i, url in enumerate(urls):
            try:
                resp = requests.get(url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AI-Agent/1.0)"
                })
                if resp.status_code == 200:
                    content_type = resp.headers.get("Content-Type", "")
                    if any(t in content_type for t in ("text/html", "text/plain", "application/json")):
                        text = self._extract_text(resp.text)
                        combined.append(f"=== URL [{i+1}/{len(urls)}]: {url} ===\n{text[:3000]}\n")
                    else:
                        combined.append(f"=== URL [{i+1}/{len(urls)}]: {url} ===\n[Non-text content: {content_type}, {len(resp.content)} bytes]\n")
                else:
                    combined.append(f"=== URL [{i+1}/{len(urls)}]: {url} ===\n[HTTP {resp.status_code}]\n")
                log_agent_action(f"Fetched: {url}")
            except Exception as e:
                combined.append(f"=== URL [{i+1}/{len(urls)}]: {url} ===\n[Error: {e}]\n")
                log_error(f"Failed to fetch {url}: {e}")
        return "\n".join(combined)

    @staticmethod
    def _extract_text(html: str) -> str:
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', html)
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'")
        text = text.replace('&nbsp;', ' ')
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
