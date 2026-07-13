"""
MCP Tools Plugin - Adapts MCP-provided tools into BaseTool for unified ToolRegistry management.
"""

from typing import Any

from modules.agents.tool_base import BaseTool
from modules.mcp.mcp_registry import registry as mcp_registry
from modules.utils.logger import log_tool_call, log_error, log_info


class MCPToolAdapter(BaseTool):
    """Adapter that wraps an MCP tool as a BaseTool for unified registry management."""

    category = "mcp"

    def __init__(self, tool_name: str, description: str, input_schema: dict[str, Any]):
        super().__init__()
        self.name = tool_name
        self.description = description
        self.parameters = input_schema

    def execute(self, **kwargs) -> Any:
        log_tool_call(f"MCP adapter: {self.name}({kwargs})")
        try:
            result_text = mcp_registry.call_tool(self.name, kwargs)
            return result_text
        except Exception as e:
            log_error(f"MCP adapter: {self.name} failed: {e}")
            raise


def register_mcp_tools(tool_registry):
    """Scan all connected MCP servers and register their tools as MCPToolAdapters in tool_registry.

    Removes previously registered MCP adapters first (for reload scenarios).
    """
    for name in list(tool_registry._tools):
        tool = tool_registry._tools[name]
        if isinstance(tool, MCPToolAdapter):
            tool_registry.unregister(name)

    mcp_tools = mcp_registry.get_all_tools()
    count = 0
    for server_name, tools in mcp_tools.items():
        for t in tools:
            if t["name"] not in tool_registry._tools:
                adapter = MCPToolAdapter(
                    tool_name=t["name"],
                    description=t["description"],
                    input_schema=t["input_schema"],
                )
                tool_registry.register(adapter)
                count += 1
                log_info(f"MCP adapter registered: {t['name']} (from {server_name})")

    log_info(f"MCP adapter: {count} tool(s) registered into ToolRegistry")
