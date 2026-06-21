"""
MCP Client - Model Context Protocol client implementation.
Supports both SSE (server type) and stdio (local type) transports.
Implements JSON-RPC 2.0 based MCP protocol for tool discovery and invocation.
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
    MCP client supporting two transport modes:
    - "server": SSE (Server-Sent Events) transport over HTTP
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
                "intent_categories": ["research", "ppt"]
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

        # SSE-specific state
        self._sse_session: requests.Session | None = None
        self._sse_thread: threading.Thread | None = None
        self._sse_stop = threading.Event()
        self._message_url: str | None = None
        self._sse_buffer: queue.Queue[dict[str, Any]] = queue.Queue()

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
        """Establish connection to the MCP server. Returns True on success."""
        try:
            if self.transport_type == "server":
                return self._connect_sse()
            else:
                return self._connect_stdio()
        except Exception as e:
            log_error(f"MCP [{self.name}] connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from the MCP server."""
        self._connected = False
        if self.transport_type == "server":
            self._disconnect_sse()
        else:
            self._disconnect_stdio()
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

    def _send_request(self, method: str, params: object = None) -> dict[str, Any] | None:
        """Send a JSON-RPC request and wait for response."""
        req_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        # Create response queue
        resp_queue = queue.Queue()
        self._pending[req_id] = resp_queue

        try:
            if self.transport_type == "server":
                self._send_sse(msg)
            else:
                self._send_stdio(msg)

            # Wait for response with timeout
            try:
                resp = resp_queue.get(timeout=30)
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
                self._send_sse(msg)
            else:
                self._send_stdio(msg)
        except Exception as e:
            log_error(f"MCP [{self.name}] notification failed: {e}")

    def _send_sse(self, msg: dict[str, Any]):
        """Send JSON-RPC message via SSE message endpoint."""
        if not self._message_url:
            raise MCPError(-1, "No message endpoint URL")
        session = self._sse_session
        if session is None:
            raise MCPError(-1, "SSE session not initialized")
        resp = session.post(
            self._message_url,
            json=msg,
            timeout=10,
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

    def test_connection(self) -> dict[str, Any]:
        """Test the MCP connection and return diagnostic info."""
        result: dict[str, Any] = {
            "name": self.name,
            "type": self.transport_type,
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
