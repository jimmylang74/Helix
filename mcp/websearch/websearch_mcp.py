#!/usr/bin/env python3
"""
WebSearch MCP Server (stdio transport) - proxy to remote provider MCP.

Extracted from the oh-my-openagent plugin's `websearch` MCP
(packages/omo-opencode/src/mcp/websearch.ts). The plugin's MCP is purely a
*remote* config pointing at a hosted provider endpoint:

  - Exa   (default): https://mcp.exa.ai/mcp?tools=web_search_exa
  - Tavily:          https://mcp.tavily.com/mcp/

This server re-exposes that exact remote MCP over stdio (JSON-RPC 2.0 over
stdin/stdout, the same transport Helix's other built-in MCP servers use):
every incoming JSON-RPC message is forwarded to the provider's Streamable HTTP
endpoint over a persistent session, and the provider's JSON-RPC responses are
relayed back. Tool surface (e.g. `web_search_exa`) is defined entirely by the
provider, so the extraction stays faithful to the plugin.

Reads configuration from environment variables:
  WEBSEARCH_PROVIDER - "exa" (default) or "tavily"
  EXA_API_KEY        - Optional Exa API key (anonymous tier works without it)
  TAVILY_API_KEY     - Required when provider=tavily
"""

import json
import os
import sys
from typing import Any

import requests

# Configuration from environment
WEBSEARCH_PROVIDER_ENV = os.environ.get("WEBSEARCH_PROVIDER", "exa").lower()
EXA_API_KEY = os.environ.get("EXA_API_KEY", "").strip()
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

# Remote provider endpoints (identical to the plugin's remote MCP config)
REMOTE_URLS = {
    "exa": "https://mcp.exa.ai/mcp?tools=web_search_exa",
    "tavily": "https://mcp.tavily.com/mcp/",
}

HTTP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# Per-request timeouts: initialize is fast, tools/call can search the web
INIT_TIMEOUT = 15
CALL_TIMEOUT = 60

_session: requests.Session | None = None
_remote_session_id: str | None = None


def _log(message: str) -> None:
    """Diagnostics to stderr (never pollute the MCP stdout channel)."""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse an SSE response body into JSON-RPC payloads.

    Handles both SSE framing (event: message / data: {...}) and servers that
    reply with plain JSON (Content-Type: application/json).
    """
    body = body.strip()
    if not body:
        return []
    if body.startswith("{"):
        try:
            return [json.loads(body)]
        except json.JSONDecodeError:
            return []
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "" and data_lines:
            payload = "\n".join(data_lines).strip()
            data_lines = []
            if payload:
                try:
                    messages.append(json.loads(payload))
                except json.JSONDecodeError:
                    _log(f"[websearch] skipped non-JSON SSE data: {payload[:120]}")
    if data_lines:  # trailing event without closing blank line
        payload = "\n".join(data_lines).strip()
        if payload:
            try:
                messages.append(json.loads(payload))
            except json.JSONDecodeError:
                _log(f"[websearch] skipped non-JSON SSE data: {payload[:120]}")
    return messages


def _forward_remote(message: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    """Forward one JSON-RPC message to the provider endpoint, return responses."""
    global _remote_session_id
    provider = provider_name()
    url = REMOTE_URLS[provider]
    headers = dict(HTTP_HEADERS)
    if _remote_session_id:
        headers["Mcp-Session-Id"] = _remote_session_id
    if provider == "exa" and EXA_API_KEY:
        headers["Authorization"] = f"Bearer {EXA_API_KEY}"

    resp = _get_session().post(url, json=message, headers=headers, timeout=timeout)

    if message.get("method") == "initialize":
        _remote_session_id = resp.headers.get("Mcp-Session-Id") or _remote_session_id

    if resp.status_code not in (200, 201, 202):
        return [{
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {
                "code": -32000,
                "message": f"Provider MCP returned HTTP {resp.status_code}: {resp.text[:300]}",
            },
        }]
    return _parse_sse(resp.text)


def process_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Process a single JSON-RPC message from stdin, returning responses to write."""
    req_id = msg.get("id")

    # Use a shorter timeout for the handshake, a longer one for tool calls.
    timeout = CALL_TIMEOUT if msg.get("method") == "tools/call" else INIT_TIMEOUT

    try:
        responses = _forward_remote(msg, timeout)
    except requests.exceptions.Timeout:
        return [{
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": "Provider MCP timed out"},
        }]
    except requests.exceptions.RequestException as e:
        return [{
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": f"Provider MCP request failed: {e}"},
        }]

    # Relay only JSON-RPC responses (payloads carrying an id) back to the stdio
    # client; swallow server-initiated notifications (e.g. notifications/message).
    return [r for r in responses if "id" in r]


def provider_name() -> str:
    """Normalized provider from env, falling back to 'exa' for unknown values."""
    name = WEBSEARCH_PROVIDER_ENV
    if name not in REMOTE_URLS:
        _log(f"[websearch] unknown WEBSEARCH_PROVIDER '{name}' "
             f"(expected exa|tavily); defaulting to exa")
        return "exa"
    return name


def main() -> None:
    """Main loop: read JSON-RPC from stdin, write responses to stdout."""
    provider = provider_name()
    if provider == "tavily" and not TAVILY_API_KEY:
        _log("[websearch] TAVILY_API_KEY is required for provider=tavily; "
             "tool calls will fail until it is set")

    _log(f"[websearch] provider={provider} endpoint={REMOTE_URLS[provider]}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg: dict[str, Any] = {}
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            error_resp: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: invalid JSON"},
            }
            try:
                import re
                id_match = re.search(r'"id"\s*:\s*(\d+)', line)
                if id_match:
                    error_resp["id"] = int(id_match.group(1))
            except Exception:
                pass
            sys.stdout.write(json.dumps(error_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        try:
            responses = process_message(msg)
        except Exception as e:
            responses = [{
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": f"Internal error: {e}"},
            }]
        for resp in responses:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()