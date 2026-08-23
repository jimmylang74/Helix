"""
Host 侧工具装配模块。

HelixCore 只提供 ToolRegistry 类型与内存态注册表；本模块负责全部
"知道外部世界"的装配动作：

- discover_plugins: 扫描 plugins/ 与 plugins/user/ 目录，实例化 BaseTool 子类并注册
- load_tool_config: 读取 Helix.json 的 plugins 段，应用 enabled/intents
- save_tool_config: 将注册表启用态/意图写回 Helix.json 的 plugins 段

对应 HelixCore 侧契约：HelixCore/tools/base.py（ToolRegistry/BaseTool 类型）。
"""

import importlib
import os
import sys
from typing import Dict

from HelixCore.tools.base import BaseTool, ToolRegistry
from modules.config.config_manager import ConfigManager
from modules.utils.logger import log_error, log_info, log_warning
from modules.utils.paths import PROJECT_ROOT, project_path

_PLUGINS_DIR = project_path("plugins")
_USER_PLUGINS_DIR = os.path.join(_PLUGINS_DIR, "user")


def discover_plugins(registry: ToolRegistry) -> None:
    """
    Scan the plugins/ directory and import all tool modules.
    Each module should define tool classes that subclass BaseTool.
    Also scans plugins/user/ for external user-defined plugins.
    Skips mcp_tools.py (MCP tools are registered separately).
    """
    if not os.path.isdir(_PLUGINS_DIR):
        log_warning(f"plugin_loader: plugins directory not found: {_PLUGINS_DIR}")
        return

    # Ensure project root is importable (plugins package + plugins.user.*)
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    _scan_plugin_dir(registry, _PLUGINS_DIR, source="内部插件")

    if os.path.isdir(_USER_PLUGINS_DIR):
        if _USER_PLUGINS_DIR not in sys.path:
            sys.path.insert(0, _USER_PLUGINS_DIR)
        _scan_plugin_dir(registry, _USER_PLUGINS_DIR, source="外部插件")


def _scan_plugin_dir(registry: ToolRegistry, plugin_dir: str, source: str) -> None:
    """Scan a plugin directory and register discovered tools."""
    for filename in sorted(os.listdir(plugin_dir)):
        if filename.startswith("_") or not filename.endswith(".py"):
            continue
        # Skip mcp_tools.py in the main plugins dir (registered separately)
        if source == "内部插件" and filename == "mcp_tools.py":
            continue

        if source == "外部插件":
            module_name = f"plugins.user.{filename[:-3]}"
        else:
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
                        instance.source = source
                        registry.register(instance)
                    except Exception as e:
                        log_error(f"plugin_loader: failed to instantiate {attr_name}: {e}")
            log_info(f"plugin_loader: loaded plugin module '{module_name}' (source={source})")
        except Exception as e:
            log_error(f"plugin_loader: failed to import '{module_name}': {e}")


def load_tool_config(registry: ToolRegistry) -> None:
    """Load enable/disable state and intents from Helix.json (plugins section)."""
    try:
        config = ConfigManager()
        tools_config = config.get("plugins", {})
        for name, tool in registry.get_all().items():
            tool_cfg = tools_config.get(name, {})
            tool.enabled = tool_cfg.get("enabled", True)
            saved_intents = tool_cfg.get("intents")
            if saved_intents is not None:
                tool.intents = saved_intents
        log_info("plugin_loader: loaded tool config from Helix.json")
    except Exception as e:
        log_warning(f"plugin_loader: failed to load tool config: {e}")


def save_tool_config(registry: ToolRegistry) -> None:
    """Persist tool config (enabled + intents) to Helix.json (plugins section)."""
    try:
        config = ConfigManager()
        tools_config = config.get("plugins", {})
        for name, tool in registry.get_all().items():
            if name not in tools_config:
                tools_config[name] = {}
            tools_config[name]["enabled"] = tool.enabled
            tools_config[name]["intents"] = list(tool.intents)
        config.update_section("plugins", tools_config)
    except Exception as e:
        log_error(f"plugin_loader: failed to save tool config: {e}")
