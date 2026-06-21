"""
Tool Base Class and ToolRegistry.
All tool calling functions are implemented as subclasses of BaseTool.
ToolRegistry manages tool lifecycle, discovery, and enable/disable state.
"""

import os
import json
import importlib
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from modules.utils.logger import log_info, log_error, log_warning


class BaseTool(ABC):
    """
    Abstract base class for all tools.

    Subclasses must implement:
      - name (str): Unique tool identifier
      - description (str): Human-readable description
      - category (str): Tool category (e.g., 'web', 'image', 'ppt', 'code', 'shell')
      - parameters (dict): JSON Schema for tool parameters
      - execute(**kwargs): The tool's main logic
    """

    name: str = ""
    description: str = ""
    category: str = ""
    parameters: Dict[str, Any] = {}

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
            "category": self.category,
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
    Singleton registry for all plugin tools.

    - Auto-discovers tools from plugins/ directory
    - Manages enable/disable state (persisted in config)
    - Provides tool lookup and execution
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
        self._tools: Dict[str, BaseTool] = {}
        self._tools_lock = threading.Lock()
        self._plugins_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "plugins"
        )

    def register(self, tool: BaseTool):
        """Register a tool instance."""
        with self._tools_lock:
            if tool.name in self._tools:
                log_warning(f"ToolRegistry: tool '{tool.name}' already registered, overwriting")
            self._tools[tool.name] = tool
            log_info(f"ToolRegistry: registered '{tool.name}' (category={tool.category})")

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
        """Get all tools as serialized dicts."""
        with self._tools_lock:
            return [tool.to_dict() for tool in self._tools.values()]

    def get_by_category(self, category: str) -> List[BaseTool]:
        """Get all tools in a category."""
        with self._tools_lock:
            return [t for t in self._tools.values() if t.category == category]

    def get_enabled_tools(self) -> List[BaseTool]:
        """Get all enabled tools."""
        with self._tools_lock:
            return [t for t in self._tools.values() if t.enabled]

    def get_categories(self) -> List[str]:
        """Get all unique categories."""
        with self._tools_lock:
            return list(set(t.category for t in self._tools.values()))

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

    def discover_plugins(self):
        """
        Scan the plugins/ directory and import all tool modules.
        Each module should define tool classes that subclass BaseTool.
        """
        if not os.path.isdir(self._plugins_dir):
            log_warning(f"ToolRegistry: plugins directory not found: {self._plugins_dir}")
            return

        # Ensure plugins/ is importable
        project_root = os.path.dirname(self._plugins_dir)
        if project_root not in __import__("sys").path:
            __import__("sys").path.insert(0, project_root)

        for filename in sorted(os.listdir(self._plugins_dir)):
            if filename.startswith("_") or not filename.endswith(".py"):
                continue
            module_name = f"plugins.{filename[:-3]}"
            try:
                module = importlib.import_module(module_name)
                # Find all BaseTool subclasses in the module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type)
                            and issubclass(attr, BaseTool)
                            and attr is not BaseTool
                            and getattr(attr, "name", "")):
                        try:
                            instance = attr()
                            self.register(instance)
                        except Exception as e:
                            log_error(f"ToolRegistry: failed to instantiate {attr_name}: {e}")
                log_info(f"ToolRegistry: loaded plugin module '{module_name}'")
            except Exception as e:
                log_error(f"ToolRegistry: failed to import '{module_name}': {e}")

    def load_enabled_state(self):
        """Load enable/disable state from config."""
        try:
            from modules.config.config_manager import ConfigManager
            config = ConfigManager()
            tools_config = config.get("plugins", {})
            with self._tools_lock:
                for name, tool in self._tools.items():
                    tool_cfg = tools_config.get(name, {})
                    tool.enabled = tool_cfg.get("enabled", True)
            log_info("ToolRegistry: loaded enabled/disabled state from config")
        except Exception as e:
            log_warning(f"ToolRegistry: failed to load enabled state: {e}")

    def save_enabled_state(self):
        """Persist current enable/disable state to config."""
        try:
            from modules.config.config_manager import ConfigManager
            config = ConfigManager()
            tools_config = config.get("plugins", {})
            with self._tools_lock:
                for name, tool in self._tools.items():
                    if name not in tools_config:
                        tools_config[name] = {}
                    tools_config[name]["enabled"] = tool.enabled
            config.update_section("plugins", tools_config)
        except Exception as e:
            log_error(f"ToolRegistry: failed to save enabled state: {e}")

    def initialize(self):
        """Full initialization: discover plugins + load state."""
        self.discover_plugins()
        self.load_enabled_state()
        log_info(f"ToolRegistry: initialized with {len(self._tools)} tool(s)")


class ToolNotFoundError(Exception):
    """Raised when a tool is not found in the registry."""
    pass


class ToolDisabledError(Exception):
    """Raised when a disabled tool is called."""
    pass


# Global registry instance
tool_registry = ToolRegistry()
