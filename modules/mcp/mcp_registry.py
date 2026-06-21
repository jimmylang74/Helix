"""
MCP Registry - Manages MCP client lifecycle, tool discovery, and intent-based routing.
Loads MCP server configurations from ConfigManager and provides tools for the orchestrator.
"""

import threading
from typing import Any

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

    # ── Initialization ─────────────────────────────────────────

    def initialize(self):
        """Load and connect all enabled MCP servers from config."""
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

    def reload(self):
        """Reload all MCP servers from config (disconnect + reconnect)."""
        self.shutdown()
        self._initialized_flag = False
        self.initialize()

    def shutdown(self):
        """Disconnect all MCP clients."""
        with self._clients_lock:
            for name, client in self._clients.items():
                try:
                    client.disconnect()
                except Exception as e:
                    log_error(f"MCP Registry: error disconnecting '{name}': {e}")
            self._clients.clear()
        self._initialized_flag = False
        log_info("MCP Registry: all servers disconnected")

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
