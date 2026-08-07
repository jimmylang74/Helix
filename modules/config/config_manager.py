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
                # Migrate: service_port → rpc_port
                if "server" in self._data and "service_port" in self._data["server"]:
                    self._data["server"]["rpc_port"] = self._data["server"].pop("service_port")
                # Cleanup: generic 为固定内部意图，不写入配置文件（遗留配置可能残留）
                changed = False
                intents = self._data.get("intents")
                if isinstance(intents, dict) and "generic" in intents:
                    del intents["generic"]
                    changed = True
                # Migrate: 内置意图提示词迁入配置后，为已存在意图补缺提示词字段
                changed = self._migrate_intent_prompts() or changed
                defaults = self._defaults()
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

    def _migrate_intent_prompts(self) -> bool:
        """为已存在的内置意图（ppt/coding）补缺提示词字段。

        ppt/coding 的提示词已从代码迁入配置（intents.*.planning_prompt /
        node_prompt / finalizer_prompt）。对配置中已存在但缺少这些字段的
        意图，按工厂默认值逐字段补缺（不覆盖用户已修改的字段）。

        返回是否发生变更。
        """
        changed = False
        seed_intents = self._defaults().get("intents", {})
        intents = self._data.get("intents")
        if not isinstance(intents, dict):
            return False
        for intent_id, seed in seed_intents.items():
            cur = intents.get(intent_id)
            if not isinstance(cur, dict):
                continue
            for field, value in seed.items():
                if field not in cur or cur[field] in (None, ""):
                    cur[field] = value
                    changed = True
        return changed

    def _defaults(self) -> Dict[str, Any]:
        return {
            "server": {
                "rpc_port": 11555,
                "admin_port": 11556,
                "host": "0.0.0.0",
                "debug": True,
                "language": "zh-CN",
                "node_parallel_count": 1,
                "log_file": "debugout.log"
            },
            "default_location": {
                "city": "Nanjing"
            },
            "llm": {
                "provider": "ollama_native",
                "model": "qwen2.5:7b",
                "endpoint": "http://localhost:11434",
                "api_key": "",
                "verbose": True,
                "stream": True,
                "log_file": "llm_engine.log",
                "max_input_tokens": 32768,
                "max_graph_updates": 5,
                "planning_max_ask_rounds": 5,
                "temperature": 0.2,
                "top_p": 0.9,
                "graph": {
                    "planning": {"temperature": 0.2, "top_p": 0.9},
                    "execution": {"temperature": 0.0, "top_p": 1.0},
                    "finalizer": {"temperature": 0.5, "top_p": 0.9},
                },
            },
            "intents": {
                "ppt": {
                    "enabled": True,
                    "name": "PPT Generation",
                    "description": "Generate PPT layouts, backgrounds and content based on user-provided materials",
                    "planning_prompt": (
                        "你是资深 PPT 内容策划与设计专家,负责为任务分解提供领域指导。\n"
                        "\n"
                        "### 领域任务分解指引\n"
                        "- PPT 任务通常拆解为多个节点:内容结构规划 → 分章节内容撰写 → 图片素材(image_search)→ PPT 生成(create_ppt)\n"
                        "- 最终成品的生成节点必须使用 `create_ppt` 工具产出实际 .pptx 文件\n"
                        "- 图片素材需求使用 `image_search` 工具搜索\n"
                        "- 内容节点应产出结构清晰、可直接交给 create_ppt 的文本\n"
                        "\n"
                        "### 强制约束\n"
                        "- 你负责的是任务规划,不是直接产出成品。禁止直接输出幻灯片内容或设计方案来替代节点图\n"
                        "- `task_complete` 仅在问题完全不需要任何工具时可设为 true;PPT 生成任务必须包含调用 `create_ppt` 的节点,不得短路\n"
                        "- `tools` 字段只能使用 Available Tools 中的真实工具名,不要编造"
                    ),
                    "node_prompt": (
                        "# PPT Node Execution Agent\n"
                        "\n"
                        "你是 PPT 生成 Agent。当前正在执行任务图中的一个节点。\n"
                        "\n"
                        "## 你的工作方式\n"
                        "1. 根据当前节点的任务描述完成任务\n"
                        "2. 涉及 PPT 创建的节点使用 create_ppt 等工具\n"
                        "3. 图片搜索使用 image_search 工具\n"
                        "4. 一次尽可能多地返回需要调用的工具列表\n"
                        "\n"
                        "## 设计准则\n"
                        "- 专业简洁的布局\n"
                        "- 一致的视觉层次\n"
                        "- 可读性好的排版\n"
                        "\n"
                        "{available_tools}\n"
                        "\n"
                        "## 提问决策规则\n"
                        "{ask_user_rules}"
                    ),
                    "finalizer_prompt": (
                        "# AI Agent — Task Finalizer\n"
                        "\n"
                        "你是 AI Agent 总结分析专家。你的任务是将所有节点的执行结果汇总为用户可以直接使用的最终答案。\n"
                        "\n"
                        "- PPT 任务：输出一个结构清晰的 PPT 设计说明"
                    ),
                },
                "coding": {
                    "enabled": True,
                    "name": "Code Generation",
                    "description": "Generate code based on user requirements with basic testing and validation",
                    "planning_prompt": (
                        "你是资深软件架构师,负责为任务分解提供领域指导。\n"
                        "\n"
                        "### 领域任务分解指引\n"
                        "- 编码任务通常拆解为多个节点:需求分析 → 代码实现 → 测试验证 → 文件保存\n"
                        "- 代码保存使用 `save_code` 工具,运行验证使用 `run_code` 工具,文件操作使用 `write_file`/`read_file`/`bash`\n"
                        "- 实现节点产出可运行的完整文件,验证节点需给出测试结果\n"
                        "\n"
                        "### 强制约束\n"
                        "- 你负责的是任务规划,不是直接产出代码。禁止直接输出代码或实现方案来替代节点图\n"
                        "- `task_complete` 仅在问题完全不需要任何工具时可设为 true;编码任务必须包含保存/验证工具调用的节点,不得短路\n"
                        "- `tools` 字段只能使用 Available Tools 中的真实工具名,不要编造"
                    ),
                    "node_prompt": (
                        "# Coding Node Execution Agent\n"
                        "\n"
                        "你是高级软件工程师 Agent。当前正在执行任务图中的一个节点。\n"
                        "\n"
                        "## 你的工作方式\n"
                        "1. 根据当前节点的任务描述完成开发工作\n"
                        "2. 使用 bash/read_file/write_file 等工具\n"
                        "3. 代码完成后执行测试验证\n"
                        "4. 一次尽可能多地返回需要调用的工具列表\n"
                        "\n"
                        "## 工程标准\n"
                        "- 写干净、可维护、生产级质量的代码\n"
                        "- 包含错误处理和边界情况\n"
                        "- 写完代码后进行测试验证\n"
                        "\n"
                        "{available_tools}\n"
                        "\n"
                        "## 提问决策规则\n"
                        "{ask_user_rules}"
                    ),
                    "finalizer_prompt": (
                        "# AI Agent — Task Finalizer\n"
                        "\n"
                        "你是 AI Agent 总结分析专家。你的任务是将所有节点的执行结果汇总为用户可以直接使用的最终答案。\n"
                        "\n"
                        "- 编码任务：输出生成的代码和说明"
                    ),
                }
            },
            "mcp_servers": {
                "searxng": {
                    "type": "local",
                    "enabled": True,
                    "command": "python3",
                    "args": ["mcp/searxng_server.py"],
                    "intent_categories": ["generic"],
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
                    "intent_categories": ["ppt", "generic"],
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
        """Get current LLM configuration (flat ai_engine-style)."""
        return {
            "provider": self.get("llm.provider", "ollama_native"),
            "model": self.get("llm.model", "qwen2.5:7b"),
            "endpoint": self.get("llm.endpoint", "http://localhost:11434"),
            "api_key": self.get("llm.api_key", ""),
            "verbose": self.get("llm.verbose", True),
            "stream": self.get("llm.stream", True),
            "log_file": self.get("llm.log_file", "llm_engine.log"),
            "max_input_tokens": self.get("llm.max_input_tokens", 32768),
            "max_graph_updates": self.get("llm.max_graph_updates", 5),
            "temperature": self.get("llm.temperature", 0.2),
            "top_p": self.get("llm.top_p", 0.9),
        }

    def get_graph_sampling(self, phase: str) -> Dict[str, float]:
        """Get per-phase graph sampling params (temperature/top_p).

        ``phase`` is one of "planning" / "execution" / "finalizer".
        """
        defaults = {
            "planning": {"temperature": 0.2, "top_p": 0.9},
            "execution": {"temperature": 0.0, "top_p": 1.0},
            "finalizer": {"temperature": 0.5, "top_p": 0.9},
        }
        base = defaults.get(phase, defaults["planning"])
        return {
            "temperature": float(self.get(f"llm.graph.{phase}.temperature", base["temperature"])),
            "top_p": float(self.get(f"llm.graph.{phase}.top_p", base["top_p"])),
        }

    def get_rpc_port(self) -> int:
        return self.get("server.rpc_port", 11555)

    def get_admin_port(self) -> int:
        return self.get("server.admin_port", 11556)

    def get_host(self) -> str:
        return self.get("server.host", "0.0.0.0")

    def is_debug(self) -> bool:
        return self.get("server.debug", True)
