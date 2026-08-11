"""
MCP Client - Model Context Protocol client implementation.
Supports three transport modes:
- SSE (Server-Sent Events) transport over HTTP
- Streamable HTTP transport (MCP 2025-03-26 spec)
- stdio transport via subprocess

Implements JSON-RPC 2.0 based MCP protocol for tool discovery and invocation.
Auto-detects Streamable HTTP vs SSE when connecting to server type.
"""

import json
import os
import queue
import threading
import subprocess
from typing import Any
from urllib.parse import urljoin

import requests
from modules.utils.logger import log_error, log_info, log_tool_call, log_warning

# MCP Protocol Version
MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    """MCP protocol error."""
    def __init__(self, code: int, message: str, data: object = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[MCP Error {code}] {message}")


class MCPTool:
    """Represents a tool exposed by an MCP server."""
    def __init__(self, name: str, description: str, input_schema: dict[str, Any]):
        self.name = name
        self.description = description
        self.input_schema = input_schema

    def to_tool_definition(self) -> dict[str, Any]:
        """Convert to ToolDefinition-compatible dict for LLM tool calling."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }

    def __repr__(self) -> str:
        return f"MCPTool(name={self.name})"


class MCPClient:
    """
    MCP client supporting three transport modes:
    - "server": Auto-detects Streamable HTTP or SSE transport over HTTP
    - "local": stdio transport via subprocess
    """

    def __init__(self, name: str, config: dict[str, Any]):
        """
        Args:
            name: Unique name for this MCP server connection
            config: {
                "type": "server" or "local",
                "url": "http://..." (for server type),
                "command": "python3" (for local type),
                "args": ["script.py"],
                "env": {"KEY": "VALUE"},
                "enabled": True,
                "intent_categories": ["generic"]
            }
        """
        self.name = name
        self.config = config
        self.transport_type: str = config.get("type", "local")
        self._connected = False
        self._tools: list[MCPTool] = []
        self._lock = threading.Lock()
        self._request_id = 0
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}

        # Active transport protocol: "streamable_http" or "sse" (for server type)
        self._active_protocol: str | None = None

        # SSE-specific state
        self._sse_session: requests.Session | None = None
        self._sse_thread: threading.Thread | None = None
        self._sse_stop = threading.Event()
        self._message_url: str | None = None
        self._sse_buffer: queue.Queue[dict[str, Any]] = queue.Queue()

        # Streamable HTTP-specific state
        self._http_session: requests.Session | None = None
        self._http_session_id: str | None = None
        self._http_endpoint: str | None = None

        # STDIO-specific state
        self._process: subprocess.Popen[str] | None = None
        self._stdio_thread: threading.Thread | None = None
        self._stdio_buffer: queue.Queue[dict[str, Any]] = queue.Queue()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def intent_categories(self) -> list[str]:
        return list(self.config.get("intent_categories", []))

    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    # ── Connection Management ──────────────────────────────────

    def connect(self) -> bool:
        """Establish connection to the MCP server. Returns True on success.

        Idempotent: disconnects first if already connected, so it can be
        called repeatedly by the reconnect monitor. On failure all partial
        transport state is torn down to avoid leaking threads/subprocesses
        across repeated reconnect attempts.
        """
        if self._connected:
            self.disconnect()
        try:
            if self.transport_type == "server":
                ok = self._connect_auto()
            else:
                ok = self._connect_stdio()
        except Exception as e:
            log_error(f"MCP [{self.name}] connection failed: {e}")
            ok = False
        if not ok:
            self.disconnect()
        return ok

    def disconnect(self):
        """Disconnect from the MCP server."""
        self._connected = False
        self._tools = []
        if self.transport_type == "server":
            if self._active_protocol == "streamable_http":
                self._disconnect_streamable_http()
            else:
                self._disconnect_sse()
        else:
            self._disconnect_stdio()
        self._active_protocol = None
        log_info(f"MCP [{self.name}] disconnected")

    def is_connected(self) -> bool:
        return self._connected

    # ── SSE Transport ──────────────────────────────────────────

    def _connect_sse(self) -> bool:
        """Connect via SSE transport."""
        base_url = self.config.get("url", "").rstrip("/")
        if not base_url:
            log_error(f"MCP [{self.name}] no URL configured for server type")
            return False

        sse_url = f"{base_url}/sse"
        log_info(f"MCP [{self.name}] connecting via SSE: {sse_url}")

        self._sse_stop.clear()
        self._sse_session = requests.Session()

        try:
            # Start SSE listener in background thread
            self._sse_thread = threading.Thread(
                target=self._sse_listener,
                args=(sse_url,),
                daemon=True,
            )
            self._sse_thread.start()

            # Wait for endpoint event from server (with timeout)
            timeout = 10
            try:
                endpoint_data = self._sse_buffer.get(timeout=timeout)
                if endpoint_data and endpoint_data.get("type") == "endpoint":
                    self._message_url = urljoin(base_url, endpoint_data["data"])
                    log_info(f"MCP [{self.name}] message endpoint: {self._message_url}")
                else:
                    log_error(f"MCP [{self.name}] unexpected SSE event: {endpoint_data}")
                    return False
            except queue.Empty:
                log_error(f"MCP [{self.name}] timeout waiting for SSE endpoint")
                return False

            # Send initialize request via POST
            init_result = self._send_request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "helix-mcp",
                    "version": "1.0.0",
                },
            })
            if not init_result:
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized")

            self._connected = True
            log_info(f"MCP [{self.name}] SSE connection established")
            return True

        except Exception as e:
            log_error(f"MCP [{self.name}] SSE connect failed: {e}")
            return False

    def _sse_listener(self, url: str):
        """Background thread: reads SSE stream and queues events."""
        session = self._sse_session
        if session is None:
            log_error(f"MCP [{self.name}] SSE session is None")
            return
        try:
            with session.get(url, stream=True, timeout=30) as resp:
                event_type = ""
                event_data = ""
                for line in resp.iter_lines(decode_unicode=True):
                    if self._sse_stop.is_set():
                        break
                    if line is None:
                        continue
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        event_data = line[6:].strip()
                    elif line == "":
                        # Empty line = end of event
                        if event_type == "endpoint":
                            self._sse_buffer.put({"type": "endpoint", "data": event_data})
                        elif event_type == "message":
                            try:
                                msg = json.loads(event_data)
                                msg_id = msg.get("id")
                                if msg_id is not None and msg_id in self._pending:
                                    self._pending[msg_id].put(msg)
                            except json.JSONDecodeError:
                                pass
                        event_type = ""
                        event_data = ""
        except Exception as e:
            if not self._sse_stop.is_set():
                log_error(f"MCP [{self.name}] SSE listener error: {e}")

    def _disconnect_sse(self):
        """Disconnect SSE transport."""
        self._sse_stop.set()
        if self._sse_session:
            self._sse_session.close()

    # ── Auto-Detection ────────────────────────────────────────

    def _connect_auto(self) -> bool:
        """Auto-detect transport: try Streamable HTTP first, fallback to SSE."""
        base_url = self.config.get("url", "").rstrip("/")
        if not base_url:
            log_error(f"MCP [{self.name}] no URL configured for server type")
            return False

        log_info(f"MCP [{self.name}] auto-detecting transport for: {base_url}")

        if self._try_streamable_http(base_url):
            self._active_protocol = "streamable_http"
            return True

        log_info(f"MCP [{self.name}] Streamable HTTP failed, trying SSE...")
        if self._connect_sse():
            self._active_protocol = "sse"
            return True

        log_error(f"MCP [{self.name}] all transports failed")
        return False

    def _try_streamable_http(self, base_url: str) -> bool:
        """Try connecting via Streamable HTTP. Returns True on success."""
        try:
            self._http_session = requests.Session()
            self._http_endpoint = base_url
            self._active_protocol = "streamable_http"

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }

            init_msg = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "helix-mcp",
                        "version": "1.0.0",
                    },
                },
            }

            resp = self._http_session.post(
                base_url,
                json=init_msg,
                headers=headers,
                timeout=10,
            )

            if resp.status_code == 202:
                # Session mode: the server accepted the request and assigned a
                # session. The initialize result is delivered in the response
                # body as an SSE stream, which must be read to receive it.
                self._http_session_id = resp.headers.get("Mcp-Session-Id")
                log_info(f"MCP [{self.name}] Streamable HTTP accepted (202), session: {self._http_session_id}")
                init_result = self._read_stream_response(resp, init_msg["id"], timeout=10)
                if init_result is not None and "error" in init_result:
                    log_error(f"MCP [{self.name}] Streamable HTTP init error: {init_result['error']}")
                    self._disconnect_streamable_http()
                    return False
                self._connected = True
                self._send_notification("notifications/initialized")
                log_info(f"MCP [{self.name}] Streamable HTTP (session mode) connection established")
                return True

            if resp.status_code not in (200, 201):
                log_info(f"MCP [{self.name}] Streamable HTTP rejected: {resp.status_code}")
                self._disconnect_streamable_http()
                return False

            self._http_session_id = resp.headers.get("Mcp-Session-Id")

            init_result = self._read_stream_response(resp, init_msg["id"], timeout=10)
            if init_result is None:
                log_warning(f"MCP [{self.name}] Streamable HTTP init: no response in body, continuing")
            elif "error" in init_result:
                log_error(f"MCP [{self.name}] Streamable HTTP init error: {init_result['error']}")
                self._disconnect_streamable_http()
                return False

            self._connected = True
            self._send_notification("notifications/initialized")
            log_info(f"MCP [{self.name}] Streamable HTTP connection established")
            return True

        except requests.exceptions.ConnectionError:
            log_info(f"MCP [{self.name}] Streamable HTTP connection refused")
            self._disconnect_streamable_http()
            return False
        except requests.exceptions.Timeout:
            log_info(f"MCP [{self.name}] Streamable HTTP timeout")
            self._disconnect_streamable_http()
            return False
        except Exception as e:
            log_info(f"MCP [{self.name}] Streamable HTTP failed: {e}")
            self._disconnect_streamable_http()
            return False

    def _handle_streamable_http_sse_response(self, resp: requests.Response, stop_msg_id: int | None = None):
        """Parse an SSE stream from a Streamable HTTP response body.

        Each SSE event is framed by one or more ``data:`` lines terminated by a
        blank line; a single event may span multiple data lines, which are
        joined with a newline per the SSE spec. Complete events are routed to
        the pending queue of their JSON-RPC id. Stops reading once the response
        for stop_msg_id has been received (guards against servers that keep the
        response stream open after delivering the response).
        """
        data_lines: list[str] = []
        for line in resp.iter_lines(decode_unicode=True):
            if line is None:
                continue
            if line.startswith("data:"):
                # Accept both "data: value" and "data:value" framing.
                data_lines.append(line[5:].strip())
            elif line == "" and data_lines:
                if self._handle_sse_event("\n".join(data_lines), stop_msg_id):
                    return
                data_lines = []
        if data_lines:
            self._handle_sse_event("\n".join(data_lines), stop_msg_id)

    def _handle_sse_event(self, data: str, stop_msg_id: int | None = None) -> bool:
        """Route a single SSE event payload to its pending request queue.

        Returns True if the event was the response for stop_msg_id.
        """
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            return False
        if not isinstance(msg, dict):
            return False
        msg_id = msg.get("id")
        if msg_id is not None and msg_id in self._pending:
            self._pending[msg_id].put(msg)
            if stop_msg_id is not None and msg_id == stop_msg_id:
                return True
        return False

    def _handle_streamable_http_json_response(self, resp: requests.Response, msg_id: int | None) -> bool:
        """Parse the response body as a pure JSON-RPC message and route it to
        the pending queue of its id. Returns True if the body was valid JSON.
        """
        try:
            result = resp.json()
        except (json.JSONDecodeError, ValueError):
            return False
        if isinstance(result, dict):
            resp_id = result.get("id")
            if resp_id is not None and resp_id in self._pending:
                self._pending[resp_id].put(result)
            elif msg_id is not None and msg_id in self._pending:
                self._pending[msg_id].put(result)
        return True

    def _parse_response_body(self, resp: requests.Response, msg_id: int | None = None):
        """Parse a Streamable HTTP response body and route the JSON-RPC
        response for msg_id to its pending queue.

        The body may be a pure JSON document or an SSE-framed payload. The
        server's Content-Type header decides which format to expect:
        - "application/json"  -> pure JSON body
        - "text/event-stream" -> SSE-framed body
        - anything else       -> try pure JSON first, fall back to SSE parsing
        """
        if not resp.content:
            return

        content_type = (resp.headers.get("Content-Type") or "").lower()

        if "text/event-stream" in content_type:
            self._handle_streamable_http_sse_response(resp, stop_msg_id=msg_id)
            return

        if "application/json" in content_type:
            if not self._handle_streamable_http_json_response(resp, msg_id):
                # Some servers frame SSE payloads with an application/json
                # Content-Type; fall back to SSE parsing.
                self._handle_streamable_http_sse_response(resp, stop_msg_id=msg_id)
            return

        if not self._handle_streamable_http_json_response(resp, msg_id):
            self._handle_streamable_http_sse_response(resp, stop_msg_id=msg_id)

    def _read_stream_response(self, resp: requests.Response, msg_id: int, timeout: float = 10) -> dict[str, Any] | None:
        """Parse a Streamable HTTP response body and wait for the JSON-RPC
        response carrying msg_id. Returns the response message, or None on
        timeout. Used for the initialize handshake, which is not sent through
        _send_request.
        """
        resp_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pending[msg_id] = resp_queue
        try:
            self._parse_response_body(resp, msg_id)
            try:
                return resp_queue.get(timeout=timeout)
            except queue.Empty:
                return None
        finally:
            self._pending.pop(msg_id, None)

    def _send_streamable_http(self, msg: dict[str, Any], timeout: float = 120):
        """Send JSON-RPC message via Streamable HTTP."""
        if not self._http_session or not self._http_endpoint:
            raise MCPError(-1, "Streamable HTTP session not initialized")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._http_session_id:
            headers["Mcp-Session-Id"] = self._http_session_id

        resp = self._http_session.post(
            self._http_endpoint,
            json=msg,
            headers=headers,
            timeout=timeout,
        )

        if resp.status_code == 202:
            # Session mode: the JSON-RPC response is delivered in the response
            # body as an SSE stream. Read it so the caller receives its
            # response. Notifications (no id) expect no response, so their
            # stream is not consumed to avoid blocking on a long-lived body.
            if msg.get("id") is not None:
                self._parse_response_body(resp, msg.get("id"))
            return

        if resp.status_code not in (200, 201):
            raise MCPError(-1, f"Streamable HTTP send failed: HTTP {resp.status_code}")

        self._parse_response_body(resp, msg.get("id"))

    def _disconnect_streamable_http(self):
        """Disconnect Streamable HTTP transport."""
        if self._http_session and self._http_session_id and self._http_endpoint:
            try:
                headers = {"Mcp-Session-Id": self._http_session_id}
                self._http_session.delete(self._http_endpoint, headers=headers, timeout=5)
            except Exception:
                pass
        if self._http_session:
            self._http_session.close()
        self._http_session = None
        self._http_session_id = None
        self._http_endpoint = None
        self._active_protocol = None

    # ── STDIO Transport ────────────────────────────────────────

    def _connect_stdio(self) -> bool:
        """Connect via stdio transport (subprocess)."""
        command = self.config.get("command", "")
        args = self.config.get("args", [])
        env_vars = self.config.get("env", {})

        if not command:
            log_error(f"MCP [{self.name}] no command configured for local type")
            return False

        # Build environment
        proc_env = os.environ.copy()
        proc_env["MCP_SERVER_NAME"] = self.name
        for k, v in env_vars.items():
            proc_env[k] = str(v)

        log_info(f"MCP [{self.name}] starting subprocess: {command} {' '.join(args)}")

        try:
            self._process = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env,
                text=True,
                bufsize=1,  # Line-buffered
            )

            # Start stdout reader thread
            self._stdio_buffer = queue.Queue()
            self._stdio_thread = threading.Thread(
                target=self._stdio_reader,
                daemon=True,
            )
            self._stdio_thread.start()

            # Send initialize request
            init_result = self._send_request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "helix-mcp",
                    "version": "1.0.0",
                },
            })
            if not init_result:
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized")

            self._connected = True
            log_info(f"MCP [{self.name}] stdio connection established")
            return True

        except Exception as e:
            log_error(f"MCP [{self.name}] stdio connect failed: {e}")
            return False

    def _stdio_reader(self):
        """Background thread: reads stdout lines from subprocess."""
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    msg_id = msg.get("id")
                    if msg_id is not None and msg_id in self._pending:
                        self._pending[msg_id].put(msg)
                except json.JSONDecodeError:
                    log_warning(f"MCP [{self.name}] invalid JSON from subprocess: {line[:200]}")
        except Exception as e:
            if self._connected:
                log_error(f"MCP [{self.name}] stdio reader error: {e}")

    def _disconnect_stdio(self):
        """Disconnect stdio transport."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

    # ── JSON-RPC Message Handling ──────────────────────────────

    def _send_request(self, method: str, params: object = None, timeout: float = 120) -> dict[str, Any] | None:
        """Send a JSON-RPC request and wait for response."""
        req_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        resp_queue = queue.Queue()
        self._pending[req_id] = resp_queue

        try:
            if self.transport_type == "server":
                if self._active_protocol == "streamable_http":
                    self._send_streamable_http(msg, timeout=timeout)
                else:
                    self._send_sse(msg, timeout=timeout)
            else:
                self._send_stdio(msg)

            try:
                resp = resp_queue.get(timeout=timeout)
            except queue.Empty:
                log_error(f"MCP [{self.name}] request timeout: {method}")
                return None

            if "error" in resp:
                err = resp["error"]
                log_error(f"MCP [{self.name}] request error: {method} -> {err}")
                return None

            return resp.get("result")

        finally:
            self._pending.pop(req_id, None)

    def _send_notification(self, method: str, params: object = None):
        """Send a JSON-RPC notification (no response expected)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        try:
            if self.transport_type == "server":
                if self._active_protocol == "streamable_http":
                    self._send_streamable_http(msg)
                else:
                    self._send_sse(msg)
            else:
                self._send_stdio(msg)
        except Exception as e:
            log_error(f"MCP [{self.name}] notification failed: {e}")

    def _send_sse(self, msg: dict[str, Any], timeout: float = 10):
        """Send JSON-RPC message via SSE message endpoint."""
        if not self._message_url:
            raise MCPError(-1, "No message endpoint URL")
        session = self._sse_session
        if session is None:
            raise MCPError(-1, "SSE session not initialized")
        resp = session.post(
            self._message_url,
            json=msg,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code not in (200, 202):
            raise MCPError(-1, f"SSE send failed: HTTP {resp.status_code}")

    def _send_stdio(self, msg: dict[str, Any]):
        """Send JSON-RPC message via subprocess stdin."""
        proc = self._process
        if proc is None or proc.stdin is None:
            raise MCPError(-1, "Subprocess not running")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()

    # ── Tool Discovery ─────────────────────────────────────────

    def list_tools(self) -> list[MCPTool]:
        """Discover tools from the MCP server."""
        if not self._connected:
            log_error(f"MCP [{self.name}] not connected")
            return []

        result = self._send_request("tools/list")
        if not result:
            log_error(f"MCP [{self.name}] tools/list failed")
            return []

        tools_data = result.get("tools", [])
        tools = []
        for t in tools_data:
            tool = MCPTool(
                name=t.get("name", "unknown"),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", t.get("parameters", {})),
            )
            tools.append(tool)

        with self._lock:
            self._tools = tools

        log_info(f"MCP [{self.name}] discovered {len(tools)} tool(s)")
        return tools

    def get_tools(self) -> list[MCPTool]:
        """Get cached tools (re-discover if not yet fetched)."""
        with self._lock:
            if not self._tools:
                return []
            return list(self._tools)

    # ── Tool Execution ─────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call a tool on the MCP server."""
        if not self._connected:
            raise MCPError(-1, f"MCP [{self.name}] not connected")

        log_tool_call(f"MCP [{self.name}] calling tool: {name}")

        result = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        if not result:
            raise MCPError(-1, f"tools/call failed for {name}")

        # Extract text content from MCP result
        content = result.get("content", [])
        text_parts = []
        for item in content:
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                text_parts.append(str(item.get("resource", "")))

        return "\n".join(text_parts)

    # ── Health Check ───────────────────────────────────────────

    def health_check(self, timeout: float = 5.0) -> bool:
        """Check whether the connection to the MCP server is still alive.

        Transport-level liveness first (cheap), then a JSON-RPC ping probe
        to catch hung or half-open connections. Used by the reconnect monitor.
        """
        if not self._connected:
            return False
        try:
            if self.transport_type == "local":
                if self._process is None or self._process.poll() is not None:
                    return False
            elif self._active_protocol == "sse":
                if self._sse_thread is None or not self._sse_thread.is_alive():
                    return False
            return self._ping(timeout=timeout)
        except Exception:
            return False

    def _ping(self, timeout: float = 5.0) -> bool:
        """Send a JSON-RPC ``ping`` and report whether the server answered.

        Any JSON-RPC response (result or error) proves the server is
        reachable; only transport failures or timeouts count as down.
        """
        if not self._connected:
            return False
        req_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "ping",
            "params": {},
        }
        resp_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pending[req_id] = resp_queue
        try:
            if self.transport_type == "server":
                if self._active_protocol == "streamable_http":
                    self._send_streamable_http(msg, timeout=timeout)
                else:
                    self._send_sse(msg, timeout=timeout)
            else:
                self._send_stdio(msg)
            try:
                resp_queue.get(timeout=timeout)
            except queue.Empty:
                return False
            return True
        except Exception:
            return False
        finally:
            self._pending.pop(req_id, None)

    def test_connection(self) -> dict[str, Any]:
        """Test the MCP connection and return diagnostic info."""
        result: dict[str, Any] = {
            "name": self.name,
            "type": self.transport_type,
            "protocol": None,
            "connected": False,
            "tools_count": 0,
            "tools": [],
            "error": None,
        }

        try:
            connected = self.connect()
            if not connected:
                result["error"] = "Failed to connect"
                return result

            result["connected"] = True
            result["protocol"] = self._active_protocol
            tools = self.list_tools()
            result["tools_count"] = len(tools)
            result["tools"] = [
                {"name": t.name, "description": t.description}
                for t in tools
            ]
        except Exception as e:
            result["error"] = str(e)
        finally:
            self.disconnect()

        return result


# ── Factory ────────────────────────────────────────────────────

def create_mcp_client(name: str, config: dict[str, Any]) -> MCPClient:
    """Create an MCP client from config dict."""
    return MCPClient(name, config)
