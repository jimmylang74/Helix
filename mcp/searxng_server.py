#!/usr/bin/env python3
"""
Built-in SearXNG MCP Server (stdio transport).

Provides web_search tool via MCP protocol.
Reads configuration from environment variables:
  SEARXNG_BASE_URL  - SearXNG instance URL (default: http://localhost:8888)
  SEARXNG_MAX_RESULTS - Max results per query (default: 10)

Implements MCP stdio transport (JSON-RPC 2.0 over stdin/stdout).
"""

import json
import os
import sys
from typing import Any

import requests

# Configuration from environment
SEARXNG_BASE_URL = os.environ.get("SEARXNG_BASE_URL", "http://localhost:8888").rstrip("/")
SEARXNG_MAX_RESULTS = int(os.environ.get("SEARXNG_MAX_RESULTS", "10"))

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
                "name": "helix-searxng",
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
                    "name": "web_search",
                    "description": "Search the web using SearXNG. Returns a list of search results with titles, URLs, and content snippets.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query string"
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of results (default: 10)",
                                "default": 10
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

    if name == "web_search":
        query = arguments.get("query", "")
        max_results = arguments.get("max_results", SEARXNG_MAX_RESULTS)
        try:
            results = _search_searxng(query, max_results)
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


def _search_searxng(query: str, max_results: int) -> list[dict[str, str]]:
    """Execute search via SearXNG API."""
    try:
        resp = requests.post(
            f"{SEARXNG_BASE_URL}/search",
            data={
                "q": query,
                "format": "json",
                "language": "zh-CN",
                "categories": "general",
                "pageno": 1,
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
        else:
            return [{
                "title": f"Search error: HTTP {resp.status_code}",
                "url": "",
                "content": f"SearXNG returned status {resp.status_code}"
            }]
    except requests.exceptions.ConnectionError:
        return [{
            "title": "Connection Error",
            "url": "",
            "content": f"Could not connect to SearXNG at {SEARXNG_BASE_URL}"
        }]
    except Exception as e:
        return [{
            "title": "Search Error",
            "url": "",
            "content": str(e)
        }]


def handle_ping(req_id: int) -> dict[str, Any]:
    """Handle ping request."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {}
    }


def process_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Process a single JSON-RPC message, returning response(s)."""
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params", {})

    # Notifications (no id) get no response
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
            # If we can identify an id, use it
            try:
                import re
                id_match = re.search(r'"id"\s*:\s*(\d+)', line)
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
