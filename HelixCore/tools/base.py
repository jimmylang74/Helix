"""
Tool Base Class and ToolRegistry.

HelixCore 只提供工具注册表的类型与内存态生命周期管理：
扫描插件目录、读取/写回 Helix.json 等装配动作由 Host 侧完成
（modules/host/plugin_loader.py）。
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from HelixCore.interface import IntentProvider, LogSink, NullLogSink


@dataclass
class ToolDefinition:
    """LLM tool catalog entry (name/description/parameters for prompt injection)."""
    name: str
    description: str
    parameters: Dict[str, Any]


class BaseTool(ABC):
    """
    Abstract base class for all tools.

    Subclasses must implement:
      - name (str): Unique tool identifier
      - description (str): Human-readable description
      - intents (list): Intent IDs this tool supports (e.g., ['generic']); ['*'] means all intents
      - parameters (dict): JSON Schema for tool parameters
      - execute(**kwargs): The tool's main logic

    Source is set by the Host assembly layer (modules/host/plugin_loader.py):
      - 'MCP': tools from MCP servers (MCPToolAdapter)
      - '内部插件': tools from plugins/ directory (excluding mcp_tools.py)
      - '外部插件': tools from plugins/user/ directory
    """

    name: str = ""
    description: str = ""
    intents: list = []
    parameters: Dict[str, Any] = {}
    source: str = "内部插件"  # overridden by subclasses / registry

    def __init__(self):
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """Execute the tool with the given arguments."""
        pass

    def to_dict(self) -> Dict[str, Any]:
        """Serialize tool metadata for API responses."""
        return {
            "name": self.name,
            "description": self.description,
            "intents": list(self.intents),
            "source": self.source,
            "parameters": self.parameters,
            "enabled": self.enabled,
        }

    def to_tool_definition(self) -> Dict[str, Any]:
        """Convert to LLM ToolDefinition-compatible dict."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """
    Registry for tools (memory-resident), instantiable per scope.

    - Provides tool registration, lookup and execution
    - Manages enable/disable state in memory
    - 装配（扫描 plugins/、读写 Helix.json 的 plugins 段）由 Host 完成：
      modules.host.plugin_loader.discover_plugins / load_tool_config / save_tool_config
    - 全局实例为模块级 ``tool_registry``；每个通道另建私有实例承载其通道工具，
      实现按通道隔离（见 modules/channels/runtime.py）。
    """

    def __init__(self, logger: Optional[LogSink] = None):
        self._tools: Dict[str, BaseTool] = {}
        self._tools_lock = threading.Lock()
        # Host 组合根注入（P4）：避免 HelixCore 反向依赖 modules.*
        self._intent_provider: Optional[IntentProvider] = None
        self._logger: LogSink = logger if logger is not None else NullLogSink()

    def set_logger(self, logger: Optional[LogSink]) -> None:
        """Host 组合根注入日志实现（P4）；None 时回退静默实现。"""
        self._logger = logger if logger is not None else NullLogSink()

    def set_intent_provider(self, provider: Optional[IntentProvider]) -> None:
        """Host 组合根注入意图提供者（P4，替代对 modules.agent 的 lazy import）。"""
        self._intent_provider = provider

    def _get_all_registered_intent_ids(self) -> List[str]:
        """Return all registered intent IDs from the injected intent provider."""
        if self._intent_provider is None:
            return []
        try:
            return list(self._intent_provider.get_registered_intents().keys())
        except Exception:
            return []

    @staticmethod
    def _resolve_effective_intents(tool: BaseTool, all_intents: List[str]) -> List[str]:
        """Resolve the effective intents for a tool, treating [] or ['*'] as all."""
        if '*' in tool.intents or not tool.intents:
            return list(all_intents)
        return list(tool.intents)

    def register(self, tool: BaseTool):
        """Register a tool instance."""
        with self._tools_lock:
            if tool.name in self._tools:
                self._logger.warning(f"ToolRegistry: tool '{tool.name}' already registered, overwriting")
            self._tools[tool.name] = tool
            self._logger.info(f"ToolRegistry: registered '{tool.name}' (source={tool.source}, intents={tool.intents})")

    def unregister(self, name: str):
        """Remove a tool by name."""
        with self._tools_lock:
            self._tools.pop(name, None)

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name."""
        with self._tools_lock:
            return self._tools.get(name)

    def get_all(self) -> Dict[str, BaseTool]:
        """Get all registered tools."""
        with self._tools_lock:
            return dict(self._tools)

    def get_all_as_list(self) -> List[Dict[str, Any]]:
        """Get all tools as serialized dicts with effective intents resolved."""
        with self._tools_lock:
            result = []
            all_intents = self._get_all_registered_intent_ids()
            for tool in self._tools.values():
                d = tool.to_dict()
                d["intents"] = self._resolve_effective_intents(tool, all_intents)
                result.append(d)
            return result

    def get_by_intent(self, intent: str) -> List[BaseTool]:
        """Get all tools that support a given intent."""
        with self._tools_lock:
            result = []
            for t in self._tools.values():
                if '*' in t.intents or not t.intents or intent in t.intents:
                    result.append(t)
            return result

    def get_enabled_tools(self) -> List[BaseTool]:
        """Get all enabled tools."""
        with self._tools_lock:
            return [t for t in self._tools.values() if t.enabled]

    def get_intents(self) -> List[str]:
        """Get all unique intent IDs across registered tools."""
        with self._tools_lock:
            all_intents = self._get_all_registered_intent_ids()
            intents = set()
            for t in self._tools.values():
                if '*' in t.intents or not t.intents:
                    intents.update(all_intents)
                else:
                    intents.update(t.intents)
            return list(intents)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a tool. Returns True if tool was found."""
        with self._tools_lock:
            tool = self._tools.get(name)
            if tool:
                tool.enabled = enabled
                return True
        return False

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Execute a tool by name with given arguments."""
        tool = self.get(name)
        if not tool:
            raise ToolNotFoundError(f"Tool '{name}' not found in registry")
        if not tool.enabled:
            raise ToolDisabledError(f"Tool '{name}' is disabled")
        return tool.execute(**(arguments or {}))


class ToolNotFoundError(Exception):
    """Raised when a tool is not found in the registry."""
    pass


class ToolDisabledError(Exception):
    """Raised when a disabled tool is called."""
    pass


# Global registry instance
tool_registry = ToolRegistry()
