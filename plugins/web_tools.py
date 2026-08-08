"""
Web Tools Plugin - Batch URL fetching.
"""

import re
import requests
from typing import List

from HelixCore.tools.base import BaseTool
from modules.utils.logger import log_tool_call, log_agent_action, log_error


class WebFetchBatchTool(BaseTool):
    """Fetch content from multiple URLs and combine."""

    name = "web_fetch_batch"
    description = "Fetch and extract text content from multiple URLs. Returns combined text."
    intents = ["generic"]
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
                        text = self._decode_content(resp)
                        text = self._extract_text(text)
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
    def _decode_content(resp: requests.Response) -> str:
        """Decode response content with proper charset detection."""
        content_type = resp.headers.get("Content-Type", "")
        
        # 1. Try charset from Content-Type header
        charset = None
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()
        
        if charset:
            try:
                return resp.content.decode(charset)
            except (UnicodeDecodeError, LookupError):
                pass
        
        # 2. Try UTF-8 first (most common)
        try:
            return resp.content.decode('utf-8')
        except UnicodeDecodeError:
            pass
        
        # 3. Try common Chinese encodings
        for encoding in ('gbk', 'gb2312', 'gb18030', 'big5', 'euc-cn'):
            try:
                return resp.content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        
        # 4. Fallback: replace errors
        return resp.content.decode('utf-8', errors='replace')

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
