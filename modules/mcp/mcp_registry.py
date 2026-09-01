"""
MCP Registry - Manages MCP client lifecycle, tool discovery, and intent-based routing.
Loads MCP server configurations from ConfigManager and provides tools for the orchestrator.
"""

import os
import socket
import subprocess
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

from modules.config.config_manager import ConfigManager
from modules.mcp.mcp_client import MCPClient, MCPTool, create_mcp_client
from modules.utils.logger import log_error, log_info, log_warning


class MCPRegistry:
    """
    Singleton registry for all MCP server connections.
    
    - Loads MCP server configs from ConfigManager
    - Manages client lifecycle (connect/disconnect)
    - Discovers tools from connected servers
    - Provides tools filtered by intent category
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.config = ConfigManager()
        self._clients: dict[str, MCPClient] = {}
        self._clients_lock = threading.Lock()
        self._initialized_flag = False
        self._lifecycle_lock = threading.RLock()
        self._reloading = False
        self._reconnect_thread: threading.Thread | None = None
        self._reconnect_stop = threading.Event()
        self.on_server_state_change: Callable[[str, bool, int], None] | None = None
        self._spawned: dict[str, subprocess.Popen[Any]] = {}

    # ── Initialization ─────────────────────────────────────────

    def initialize(self):
        """Load and connect all enabled MCP servers from config."""
        with self._lifecycle_lock:
            if self._initialized_flag:
                return

            mcp_config = self.config.get("mcp_servers", {})
            if not mcp_config:
                log_info("MCP Registry: no MCP servers configured")
                self._initialized_flag = True
                return

            for name, server_cfg in mcp_config.items():
                if not server_cfg.get("enabled", True):
                    continue
                self._register_server(name, server_cfg)

            self._initialized_flag = True
            log_info(f"MCP Registry initialized with {len(self._clients)} server(s)")

    def _register_server(self, name: str, config: dict[str, Any]) -> MCPClient | None:
        """Register and connect to a single MCP server."""
        # Built-in HTTP servers: spawn the process first so the URL is live
        # before the client tries to connect.
        self._spawn_server(name, config)
        client = create_mcp_client(name, config)
        connected = client.connect()
        if connected:
            tools = client.list_tools()
            with self._clients_lock:
                self._clients[name] = client
            log_info(f"MCP Registry: connected '{name}' ({len(tools)} tools)")
        else:
            log_error(f"MCP Registry: failed to connect '{name}'")
            # Still register but mark as not connected
            with self._clients_lock:
                self._clients[name] = client
        return client

    # ── Built-in HTTP server spawning ─────────────────────────

    def _spawn_server(self, name: str, config: dict[str, Any]) -> None:
        """Start an HTTP-based MCP server process when config has a 'spawn' block."""
        spawn = config.get("spawn")
        if not isinstance(spawn, dict) or not spawn.get("command"):
            return
        proc = self._spawned.get(name)
        if proc is not None and proc.poll() is None:
            return
        env = dict(os.environ)
        proxy = self.config.get("server.proxy", "")
        if proxy and "HTTPS_PROXY" not in env:
            env["HTTPS_PROXY"] = proxy
            env["HTTP_PROXY"] = proxy
        env["MCP_SERVER_NAME"] = name
        env.update(spawn.get("env") or {})
        try:
            cmd: list[str] = [str(spawn["command"])] + [str(a) for a in (spawn.get("args") or [])]
            # 独立会话/进程组：子进程不随 Helix 所在进程组的终端信号连带退出；
            # 回收改为显式终止 + 子进程侧 PR_SET_PDEATHSIG（Helix 死亡时内核信号兜底）
            proc = subprocess.Popen(cmd, env=env, start_new_session=True)
        except Exception as e:
            log_error(f"MCP Registry: failed to spawn server '{name}': {e}")
            return
        self._spawned[name] = proc
        address = self._url_host_port(config.get("url", ""))
        if address:
            self._wait_for_port(name, *address, float(spawn.get("wait_timeout") or 15))

    @staticmethod
    def _url_host_port(url: str) -> tuple[str, int] | None:
        try:
            parsed = urlparse(url)
            return str(parsed.hostname), int(parsed.port or 80)
        except (ValueError, TypeError):
            return None

    def _wait_for_port(self, name: str, host: str, port: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1):
                    log_info(f"MCP Registry: spawned server '{name}' ready on {host}:{port}")
                    return True
            except OSError:
                time.sleep(0.4)
        log_warning(f"MCP Registry: spawned server '{name}' not ready on {host}:{port} within {timeout}s")
        return False

    def _terminate_spawned(self) -> None:
        for name, proc in self._spawned.items():
            if proc.poll() is not None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception as e:
                log_error(f"MCP Registry: error stopping spawned server '{name}': {e}")
        self._spawned.clear()

    def reload(self):
        """Reload all MCP servers from config (disconnect + reconnect)."""
        self._reloading = True
        try:
            with self._lifecycle_lock:
                self.shutdown()
                self._initialized_flag = False
                self.initialize()
        finally:
            self._reloading = False

    def shutdown(self):
        """Disconnect all MCP clients and stop spawned built-in servers."""
        self._terminate_spawned()
        with self._clients_lock:
            for name, client in self._clients.items():
                try:
                    client.disconnect()
                except Exception as e:
                    log_error(f"MCP Registry: error disconnecting '{name}': {e}")
            self._clients.clear()
        self._initialized_flag = False
        log_info("MCP Registry: all servers disconnected")

    # ── Reconnect Monitor ─────────────────────────────────────

    def start_reconnect_monitor(self):
        """Start the periodic health-check / reconnect daemon thread."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._reconnect_stop = threading.Event()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True,
            name="mcp-reconnect-monitor",
        )
        self._reconnect_thread.start()
        log_info("MCP Registry: reconnect monitor started")

    def stop_reconnect_monitor(self):
        if self._reconnect_stop:
            self._reconnect_stop.set()
        self._reconnect_thread = None

    def _reconnect_loop(self):
        while not self._reconnect_stop.wait(self._get_reconnect_interval()):
            self._check_and_reconnect()

    def _get_reconnect_interval(self) -> float:
        try:
            val = float(self.config.get("mcp", {}).get("reconnect_interval", 10))
            return val if val > 0 else 10.0
        except Exception:
            return 10.0

    def _check_and_reconnect(self):
        """One monitor pass: health-check connected servers, reconnect dead ones."""
        if self._reloading:
            return
        if not self._initialized_flag:
            with self._lifecycle_lock:
                if not self._initialized_flag:
                    self.initialize()
        for name, client in self.get_all_clients().items():
            if not client.enabled:
                continue
            if client.is_connected():
                if not client.health_check():
                    log_warning(f"MCP Registry: '{name}' health check failed, marking disconnected")
                    client.disconnect()
                    self._notify_state_change(name, False)
            else:
                server_cfg = (self.config.get("mcp_servers", {}) or {}).get(name, {})
                if server_cfg.get("spawn"):
                    self._spawn_server(name, server_cfg)
                if client.connect():
                    tools = client.list_tools()
                    log_info(f"MCP Registry: '{name}' reconnected ({len(tools)} tools)")
                    self._notify_state_change(name, True)

    def _notify_state_change(self, name: str, connected: bool):
        if self.on_server_state_change is None:
            return
        try:
            client = self.get_client(name)
            tools_count = len(client.get_tools()) if client else 0
            self.on_server_state_change(name, connected, tools_count)
        except Exception as e:
            log_error(f"MCP Registry: state change callback failed for '{name}': {e}")

    # ── Client Management ─────────────────────────────────────

    def get_client(self, name: str) -> MCPClient | None:
        """Get a specific MCP client by name."""
        with self._clients_lock:
            return self._clients.get(name)

    def get_all_clients(self) -> dict[str, MCPClient]:
        """Get all registered MCP clients."""
        with self._clients_lock:
            return dict(self._clients)

    def get_connected_clients(self) -> dict[str, MCPClient]:
        """Get only connected MCP clients."""
        with self._clients_lock:
            return {n: c for n, c in self._clients.items() if c.is_connected()}

    # ── Tool Discovery ─────────────────────────────────────────

    def get_tools_for_intent(self, intent_type: str) -> list[MCPTool]:
        """
        Get all tools from MCP servers that match the given intent category.
        
        An MCP server is considered matching if its `intent_categories` list
        is empty (all intents) or contains the given intent_type.
        """
        if not self._initialized_flag:
            self.initialize()
        tools: list[MCPTool] = []
        with self._clients_lock:
            for name, client in self._clients.items():
                if not client.is_connected():
                    continue
                cats = client.intent_categories
                if cats and intent_type not in cats:
                    continue
                tools.extend(client.get_tools())
        return tools

    def get_all_tools(self) -> dict[str, list[dict[str, Any]]]:
        """
        Get all tools grouped by server name.
        Returns: {"server_name": [{"name": ..., "description": ..., ...}]}
        """
        if not self._initialized_flag:
            self.initialize()
        result: dict[str, list[dict[str, Any]]] = {}
        with self._clients_lock:
            for name, client in self._clients.items():
                if not client.is_connected():
                    continue
                result[name] = [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.input_schema,
                    }
                    for t in client.get_tools()
                ]
        return result

    # ── Tool Execution ─────────────────────────────────────────

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        """
        Call a tool by name across all connected MCP servers.
        Returns the first successful result.
        """
        with self._clients_lock:
            for name, client in self._clients.items():
                if not client.is_connected():
                    continue
                for tool in client.get_tools():
                    if tool.name == tool_name:
                        try:
                            return client.call_tool(tool_name, arguments)
                        except Exception as e:
                            log_error(f"MCP: tool '{tool_name}' on '{name}' failed: {e}")
                            continue
        raise MCPToolNotFoundError(f"Tool '{tool_name}' not found or all calls failed")


class MCPToolNotFoundError(Exception):
    """Raised when a requested MCP tool is not found."""
    pass


# Global registry instance
registry = MCPRegistry()
