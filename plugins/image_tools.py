"""
Image Tools Plugin - Image search and download.
"""

import os
import json
import requests
from typing import Any, Dict, List
from datetime import datetime

from modules.agents.tool_base import BaseTool
from modules.mcp.mcp_registry import registry as mcp_registry
from modules.utils.logger import log_tool_call, log_agent_action, log_error


class ImageSearchTool(BaseTool):
    """Search for images via MCP (Pexels/Unsplash)."""

    name = "image_search"
    description = "Search for images related to a query. Returns image URLs and metadata."
    category = "image"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The image search query"
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of images (default: 5)",
                "default": 5
            }
        },
        "required": ["query"]
    }

    def execute(self, query: str = "", max_results: int = 5, **kwargs) -> List[Dict[str, str]]:
        log_tool_call(f"image_search(query='{query}', max_results={max_results}) via MCP")
        try:
            result_text = mcp_registry.call_tool("image_search", {"query": query, "max_results": max_results})
            if result_text:
                results = json.loads(result_text)
                if isinstance(results, list):
                    return results
            return []
        except Exception as e:
            log_error(f"MCP image_search failed: {e}")
            return []


class ImageDownloadTool(BaseTool):
    """Download images from URLs to the download/ directory."""

    name = "image_download"
    description = "Download images from URLs to the local download/ directory. Returns saved file paths."
    category = "image"
    parameters = {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of image URLs to download"
            }
        },
        "required": ["urls"]
    }

    def execute(self, urls: List[str] = None, **kwargs) -> List[str]:
        urls = urls or []
        log_tool_call(f"image_download({len(urls)} images)")
        download_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "download"
        )
        os.makedirs(download_dir, exist_ok=True)

        saved = []
        for url in urls:
            try:
                resp = requests.get(url, timeout=30, stream=True)
                if resp.status_code == 200:
                    ext = ".jpg"
                    ct = resp.headers.get("Content-Type", "")
                    if "png" in ct:
                        ext = ".png"
                    elif "gif" in ct:
                        ext = ".gif"
                    elif "webp" in ct:
                        ext = ".webp"

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
                    safe_name = f"img_{timestamp}{ext}"
                    filepath = os.path.join(download_dir, safe_name)

                    with open(filepath, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    saved.append(filepath)
                    log_agent_action(f"Downloaded image: {filepath}")
                else:
                    log_error(f"Failed to download {url}: HTTP {resp.status_code}")
            except Exception as e:
                log_error(f"Failed to download {url}: {e}")
        return saved
