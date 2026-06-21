"""
MCP (Model Context Protocol) Module.

Provides MCP client infrastructure for connecting to external tool servers:
- SSE transport (server type): connect to MCP servers via URL
- STDIO transport (local type): spawn MCP server as subprocess

Public API:
- MCPClient: Client for a single MCP server connection
- MCPRegistry: Global registry managing all MCP connections
- MCPTool: Tool discovered from an MCP server
- create_mcp_client(): Factory function
"""

from modules.mcp.mcp_client import MCPClient, MCPTool, MCPError, create_mcp_client
from modules.mcp.mcp_registry import MCPRegistry, MCPToolNotFoundError, registry

__all__ = [
    "MCPClient",
    "MCPTool",
    "MCPError",
    "MCPRegistry",
    "MCPToolNotFoundError",
    "create_mcp_client",
    "registry",
]
