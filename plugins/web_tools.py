"""
Web Tools Plugin - Web search and batch URL fetching.
"""

import json
import re
import requests
from typing import Any, Dict, List

from modules.agents.tool_base import BaseTool
from modules.mcp.mcp_registry import registry as mcp_registry
from modules.utils.logger import log_tool_call, log_agent_action, log_error


class WebSearchTool(BaseTool):
    """Search the web via MCP (SearXNG)."""

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
        except Exception as e:
            log_error(f"MCP web_search failed: {e}")
            return []


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
