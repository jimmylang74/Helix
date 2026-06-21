#!/usr/bin/env python3
"""
Built-in Image Search MCP Server (stdio transport).

Provides image_search tool via MCP protocol.
Supports Pexels and Unsplash as image providers.
Reads configuration from environment variables:
  IMAGE_PROVIDER    - "pexels" or "unsplash" (default: pexels)
  PEXELS_API_KEY    - Pexels API key
  UNSPLASH_API_KEY  - Unsplash Access Key

Implements MCP stdio transport (JSON-RPC 2.0 over stdin/stdout).
"""

import json
import os
import sys
from typing import Any

import requests

# Configuration from environment
IMAGE_PROVIDER = os.environ.get("IMAGE_PROVIDER", "pexels")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
UNSPLASH_API_KEY = os.environ.get("UNSPLASH_API_KEY", "")

# MCP Protocol version
MCP_VERSION = "2024-11-05"


def handle_initialize(req_id: int) -> dict[str, Any]:
    """Handle initialize request."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "protocolVersion": MCP_VERSION,
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "helix-image-search",
                "version": "1.0.0"
            }
        }
    }


def handle_list_tools(req_id: int) -> dict[str, Any]:
    """Handle tools/list request."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "tools": [
                {
                    "name": "image_search",
                    "description": "Search for images related to a query. Uses Pexels or Unsplash API. Returns image URLs with photographer credit.",
                    "inputSchema": {
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
                }
            ]
        }
    }


def handle_call_tool(req_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """Handle tools/call request."""
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    if name == "image_search":
        query = arguments.get("query", "")
        max_results = arguments.get("max_results", 5)
        try:
            results = _search_images(query, max_results)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(results, ensure_ascii=False, indent=2)
                        }
                    ]
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32000,
                    "message": str(e)
                }
            }
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Tool not found: {name}"
            }
        }


def _search_images(query: str, max_results: int) -> list[dict[str, str]]:
    """Search for images using configured provider."""
    if IMAGE_PROVIDER == "pexels":
        return _search_pexels(query, max_results)
    elif IMAGE_PROVIDER == "unsplash":
        return _search_unsplash(query, max_results)
    else:
        return _mock_images(query, max_results)


def _search_pexels(query: str, max_results: int) -> list[dict[str, str]]:
    """Search via Pexels API."""
    if not PEXELS_API_KEY:
        return _mock_images(query, max_results)

    resp = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": max_results},
        headers={"Authorization": PEXELS_API_KEY},
        timeout=10
    )
    if resp.status_code == 200:
        data = resp.json()
        results = []
        for photo in data.get("photos", []):
            results.append({
                "url": photo.get("src", {}).get("original", ""),
                "small_url": photo.get("src", {}).get("medium", ""),
                "photographer": photo.get("photographer", ""),
                "source": "pexels",
                "alt": photo.get("alt", ""),
            })
        return results
    else:
        return [{
            "url": "",
            "small_url": "",
            "photographer": "",
            "source": "pexels",
            "alt": f"Pexels API returned HTTP {resp.status_code}"
        }]


def _search_unsplash(query: str, max_results: int) -> list[dict[str, str]]:
    """Search via Unsplash API."""
    if not UNSPLASH_API_KEY:
        return _mock_images(query, max_results)

    resp = requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "per_page": max_results},
        headers={"Authorization": f"Client-ID {UNSPLASH_API_KEY}"},
        timeout=10
    )
    if resp.status_code == 200:
        data = resp.json()
        results = []
        for photo in data.get("results", []):
            results.append({
                "url": photo.get("urls", {}).get("regular", ""),
                "small_url": photo.get("urls", {}).get("small", ""),
                "photographer": photo.get("user", {}).get("name", ""),
                "source": "unsplash",
                "alt": photo.get("alt_description", ""),
            })
        return results
    else:
        return [{
            "url": "",
            "small_url": "",
            "photographer": "",
            "source": "unsplash",
            "alt": f"Unsplash API returned HTTP {resp.status_code}"
        }]


def _mock_images(query: str, max_results: int) -> list[dict[str, str]]:
    """Return mock images when APIs are not configured."""
    results = []
    for i in range(min(max_results, 3)):
        results.append({
            "url": f"https://via.placeholder.com/800x600.png?text={query.replace(' ', '+')}+{i+1}",
            "small_url": f"https://via.placeholder.com/400x300.png?text={query.replace(' ', '+')}+{i+1}",
            "photographer": "Mock",
            "source": "mock",
            "alt": query,
        })
    return results


def handle_ping(req_id: int) -> dict[str, Any]:
    """Handle ping request."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {}
    }


def process_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Process a single JSON-RPC message."""
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params", {})

    if req_id is None:
        return []

    if method == "initialize":
        return [handle_initialize(req_id)]
    elif method == "tools/list":
        return [handle_list_tools(req_id)]
    elif method == "tools/call":
        return [handle_call_tool(req_id, params)]
    elif method == "ping":
        return [handle_ping(req_id)]
    else:
        return [{
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }]


def main():
    """Main loop: read JSON-RPC from stdin, write responses to stdout."""
    import re as re_module
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            responses = process_message(msg)
            for resp in responses:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            error_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error: invalid JSON"
                }
            }
            try:
                id_match = re_module.search(r'"id"\s*:\s*(\d+)', line)
                if id_match:
                    error_resp["id"] = int(id_match.group(1))
            except Exception:
                pass
            sys.stdout.write(json.dumps(error_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            error_resp = {
                "jsonrpc": "2.0",
                "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {e}"
                }
            }
            sys.stdout.write(json.dumps(error_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
