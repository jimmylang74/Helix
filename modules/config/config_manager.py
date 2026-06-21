"""
Configuration manager for AI Agent Service.
Reads/writes Helix.json configuration file.
"""

import os
import json
import threading
from typing import Any, Dict, Optional

# Default config path
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "Helix.json")


class ConfigManager:
    """Thread-safe configuration manager."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, config_path: Optional[str] = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        if self._initialized:
            return
        self._initialized = True
        self._config_path = config_path or DEFAULT_CONFIG_PATH
        self._data: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        """Load configuration from JSON file."""
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                # Merge any missing top-level keys from defaults (for upgrades)
                defaults = self._defaults()
                changed = False
                for key, val in defaults.items():
                    if key not in self._data:
                        self._data[key] = val
                        changed = True
                if changed:
                    self._save()
                from modules.utils.logger import log_info
                log_info(f"Config loaded from {self._config_path}")
            except Exception as e:
                from modules.utils.logger import log_warning
                log_warning(f"Failed to load config: {e}, using defaults")
                self._data = self._defaults()
                self._save()
        else:
            self._data = self._defaults()
            self._save()

    def _defaults(self) -> Dict[str, Any]:
        return {
            "server": {
                "service_port": 11555,
                "admin_port": 11556,
                "host": "0.0.0.0",
                "debug": True,
                "language": "zh-CN"
            },
            "llm": {
                "provider": "ollama",
                "ollama": {
                    "base_url": "http://localhost:11434",
                    "model": "qwen2.5:7b",
                    "temperature": 0.7,
                    "max_tokens": 4096
                },
                "openai": {
                    "api_key": "",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "max_tokens": 4096
                },
                "gemini": {
                    "api_key": "",
                    "model": "gemini-2.0-flash",
                    "temperature": 0.7,
                    "max_tokens": 4096
                },
                "deepseek": {
                    "api_key": "",
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "temperature": 0.7,
                    "max_tokens": 4096
                }
            },
            "tools": {
                "searxng": {
                    "enabled": False,
                    "base_url": "http://localhost:8888",
                    "max_results": 10
                },
                "image_search": {
                    "provider": "pexels",
                    "pexels": {"api_key": ""},
                    "unsplash": {"api_key": ""}
                }
            },
            "intents": {
                "ppt": {
                    "enabled": True,
                    "name": "PPT Generation",
                    "description": "Generate PPT layouts, backgrounds and content based on user-provided materials"
                },
                "research": {
                    "enabled": True,
                    "name": "Smart Search",
                    "description": "Search the web, filter and refine content to provide a final answer"
                },
                "coding": {
                    "enabled": True,
                    "name": "Code Generation",
                    "description": "Generate code based on user requirements with basic testing and validation"
                }
            },
            "mcp_servers": {
                "searxng": {
                    "type": "local",
                    "enabled": True,
                    "command": "python3",
                    "args": ["mcp/searxng_server.py"],
                    "intent_categories": ["research"],
                    "env": {
                        "SEARXNG_BASE_URL": "http://localhost:8888",
                        "SEARXNG_MAX_RESULTS": "10"
                    }
                },
                "image_search": {
                    "type": "local",
                    "enabled": True,
                    "command": "python3",
                    "args": ["mcp/image_search_server.py"],
                    "intent_categories": ["ppt", "research"],
                    "env": {
                        "IMAGE_PROVIDER": "pexels",
                        "PEXELS_API_KEY": "",
                        "UNSPLASH_API_KEY": ""
                    }
                }
            }
        }

    def _save(self):
        """Save configuration to JSON file."""
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            from modules.utils.logger import log_error
            log_error(f"Failed to save config: {e}")

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get config value by dot-separated path. E.g. 'llm.ollama.model'"""
        with self._lock:
            keys = key_path.split(".")
            value = self._data
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    return default
                if value is None:
                    return default
            return value

    def set(self, key_path: str, value: Any):
        """Set config value by dot-separated path."""
        with self._lock:
            keys = key_path.split(".")
            target = self._data
            for key in keys[:-1]:
                if key not in target:
                    target[key] = {}
                target = target[key]
            target[keys[-1]] = value
            self._save()

    def get_all(self) -> Dict[str, Any]:
        """Return entire config dict."""
        with self._lock:
            return json.loads(json.dumps(self._data))

    def update_section(self, section: str, data: Dict[str, Any]):
        """Update an entire config section."""
        with self._lock:
            if section in self._data:
                self._data[section] = data
            else:
                self._data[section] = data
            self._save()

    def get_llm_config(self) -> Dict[str, Any]:
        """Get current LLM provider configuration."""
        provider = self.get("llm.provider", "ollama")
        config = self.get(f"llm.{provider}", {})
        return {
            "provider": provider,
            **config
        }

    def get_service_port(self) -> int:
        return self.get("server.service_port", 11555)

    def get_admin_port(self) -> int:
        return self.get("server.admin_port", 11556)

    def get_host(self) -> str:
        return self.get("server.host", "0.0.0.0")

    def is_debug(self) -> bool:
        return self.get("server.debug", True)
