# HelixCore — Agent 核心包

HelixCore 是 Helix 的**核心引擎包**，承载 Agent 编排、任务图、工具基座、提示词与 Token 估算等纯核心逻辑。它与 Host（`modules/host/`）解耦，通过三个注入端口（Protocol）交互，不依赖 Flask / ai_engine / 配置管理器等任何宿主设施。

> 本文件说明 HelixCore 的基本架构、设计原则、使用方法与接口。Host 侧整体架构与 API 见 [README.md](README.md) 与 [doc/design.md](doc/design.md)。

## 1. 基本架构

### 1.1 分层与依赖方向

```
┌─────────────────────────────────────────────────────────────┐
│  Host 侧（modules/） — Flask / ai_engine / SSE / 配置 / 路由   │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 组合根（Helix.py / modules/core/orchestrator.py）       │  │
│  │   装配并注入三大端口                                    │  │
│  └───────────────┬───────────────────┬──────────────────┘  │
│                  │ 注入               │ 注入               │
└──────────────────┼───────────────────┼─────────────────────┘
                   ▼                   ▼
┌─────────────────────────────────────────────────────────────┐
│  HelixCore（核心包，零宿主依赖）                              │
│  ┌───────────┐ ┌─────────────┐ ┌────────────┐ ┌───────────┐ │
│  │ ports/    │ │ orchestrator│ │ tools/     │ │ prompts/  │ │
│  │ (协议)    │ │ (DAG/状态)   │ │ (工具基座)  │ │ (提示词)   │ │
│  └───────────┘ └─────────────┘ └────────────┘ └───────────┘ │
│  ┌───────────┐                                             │
│  │ utils/    │  Token 估算器                                │
│  └───────────┘                                             │
└─────────────────────────────────────────────────────────────┘
```

- **依赖方向单向**：Host → HelixCore。HelixCore 从不 import Host 的任何模块。
- **唯一例外**：HelixCore 允许共享 `modules.utils.logger`（日志工具，无业务依赖）。
- **禁止项**：HelixCore 内不得 import `flask`、`ai_engine`、`modules.config`、`modules.host`、`llm_events`、`status_events`、`user_question`、`history_store`、`modules.app`。

### 1.2 三大注入端口

HelixCore 通过三个 `Protocol` 端口与 Host 交互，全部由 Host 在**组合根**注入：

| 端口 | 位置 | 契约 | Host 默认实现 |
|------|------|------|---------------|
| LLMBackend | `HelixCore/ports/llm.py` | LLM 调用抽象（chat / ask_json / ask_with_tools / simple_chat / refresh / get_provider_model） | `modules/host/ai_engine_backend.py` `AIEngineBackend`（ai_engine 子模块接入） |
| EventSink | `HelixCore/ports/events.py` | 前端状态事件输出（emit / cleanup） | `modules/host/event_sink.py` `SSEEventSink`（SSE 事件总线适配） |
| IntentProvider | `HelixCore/ports/intents.py` | 意图配置查询与增删改（get_registered_intents / CRUD / get_enabled_intent_ids） | `modules/host/intent_store.py` `IntentStore`（IntentRouter 适配） |

工具注册表 `ToolRegistry` 额外注入两个可选的 Host 依赖（P4）：

- `set_intent_provider(provider)`：意图列表来源（解析 `intents=['*']` 通配工具时使用）。
- `set_config_store(store)`：工具启用状态读写（需提供 `get(key_path, default)` 与 `update_section(section, data)`，`ConfigManager` 即满足）。

### 1.3 组合根装配

- **服务入口** [Helix.py](Helix.py) `main()`：创建 `ConfigManager` → `tool_registry.set_intent_provider(intent_store)` + `set_config_store(config)` → `tool_registry.initialize()` → MCP 注册 → 启动双端口（RPC + Admin）。
- **编排器** `modules/core/orchestrator.py` `AgentOrchestrator.__init__` 接受四参数 `(llm_backend, config, event_sink, intent_provider)`，缺省时各自回退到 Host 默认实现（`AIEngineBackend` / `SSEEventSink` / `IntentStore` 等），保证独立运行与测试注入两不误。

## 2. 设计要点

### 2.1 三阶段 DAG 编排

编排核心为三阶段流水线（实现位于 Host 侧 `AgentOrchestrator`，依赖 HelixCore 的 DAG/状态/提示词构件）：

1. **Phase 1 — Task Planning**：LLM 将用户请求分解为带依赖关系的 DAG 节点图。
2. **Phase 2 — Node Loop**：按依赖解析执行节点，支持并行；节点内为「LLM 决策 → 工具调用」循环；执行中 LLM 可 `need_update_node` 动态更新图（最多 `max_graph_updates` 次）。
3. **Phase 3 — Finalizer（可选）**：汇总全部节点结果，生成最终答复。

节点级上下文隔离：每个节点维护独立对话历史与工具结果，节点间通过 `depends` 传递结果。

### 2.2 通用意图兜底

`GENERIC_INTENT_ID = "generic"`（`HelixCore/prompts/task_graph_prompts.py`）为固定内置兜底意图：恒存在、恒排最前、恒可用、禁止注册/更新/删除。`ppt` / `coding` 等意图在 `Helix.json` 配置化注册，各阶段提示词（规划/节点执行/总结）均可按意图覆盖，未配置的意图回退 generic 模板。

### 2.3 工具体系

- `BaseTool`（`HelixCore/tools/base.py`）：所有工具抽象基类。子类需实现 `name` / `description` / `intents` / `parameters` / `execute(**kwargs)`。
- `ToolRegistry`：单例注册表。自动扫描 `plugins/`（内部插件）与 `plugins/user/`（外部插件），跳过 `mcp_tools.py`（MCP 工具单独注册）；管理启用状态并持久化到 `Helix.json` 的 `plugins` 段。
- 通配意图：`intents = ['*']` 或空列表表示「所有意图可用」，注册表按注入的 IntentProvider 解析为实际意图 ID 列表。

## 3. 目录结构

```
HelixCore/
├── __init__.py                 # 包导出（EventSink / IntentProvider / LLMBackend / LLMResponse / AgentConfig / SamplingParams）
├── ports/                      # 注入端口（Protocol 契约）
│   ├── llm.py                  #   LLMBackend / LLMResponse
│   ├── events.py               #   EventSink
│   └── intents.py              #   IntentProvider
├── orchestrator/               # 编排构件
│   ├── task_graph.py           #   DAG 任务图（TaskNode / NodeState 状态机）
│   ├── agent_state.py          #   AgentState（TypedDict）+ create_initial_state()
│   └── config.py               #   AgentConfig / SamplingParams（值对象）
├── tools/
│   └── base.py                 # BaseTool 抽象基类 + ToolRegistry + tool_registry 全局实例
├── prompts/
│   └── task_graph_prompts.py   # 三阶段提示词模板 + generic 兜底 + JSON 契约
└── utils/
    └── tokenizer.py            # TokenEstimator（多 provider 估算）+ create_estimator_for_config()
```

## 4. 使用方法

### 4.1 作为依赖引入

HelixCore 是普通 Python 包，项目根目录加入 `sys.path` 后直接 import：

```python
from HelixCore.tools.base import BaseTool, ToolRegistry, tool_registry
from HelixCore.ports.llm import LLMBackend, LLMResponse
from HelixCore.ports.events import EventSink
from HelixCore.ports.intents import IntentProvider
from HelixCore.orchestrator.agent_state import AgentState, create_initial_state
from HelixCore.orchestrator.task_graph import TaskGraph, NodeState
from HelixCore.orchestrator.config import AgentConfig, SamplingParams
```

### 4.2 开发自定义工具

继承 `BaseTool`，放入 `plugins/`（内置）或 `plugins/user/`（外部插件）即可自动注册：

```python
from HelixCore.tools.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "工具说明，LLM 根据此描述决定是否调用"
    intents = ["generic"]      # 绑定意图；["*"] 表示所有意图可用
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "参数说明"}},
        "required": ["query"],
    }

    def execute(self, query: str = "", **kwargs) -> str:
        return f"结果: {query}"
```

完整指南见 [plugins/user/plugin.md](plugins/user/plugin.md)。

### 4.3 组合根注入示例（Host 侧）

```python
from HelixCore.tools.base import tool_registry
from modules.host.intent_store import intent_store
from modules.config.config_manager import ConfigManager
from modules.host.ai_engine_backend import AIEngineBackend
from modules.host.event_sink import SSEEventSink
from HelixCore.orchestrator.config import AgentConfig
from modules.core.orchestrator import AgentOrchestrator

config = ConfigManager()

# 工具注册表：注入意图提供者 + 配置存取
tool_registry.set_intent_provider(intent_store)
tool_registry.set_config_store(config)
tool_registry.initialize()

# 编排器：注入三大端口（可省略以使用默认实现）
orch = AgentOrchestrator(
    llm_backend=AIEngineBackend(),
    event_sink=SSEEventSink(),
    intent_provider=intent_store,
    config=AgentConfig(),   # 或从 Helix.json 构建
)
result = orch.process_request("用Python写一个hello world程序", forced_intent="coding")
```

### 4.4 测试注入（Host 侧）

端口均为 `@runtime_checkable` Protocol，测试中可注入替身：

```python
from HelixCore.ports.llm import LLMBackend, LLMResponse

class FakeLLM(LLMBackend):
    def chat(self, prompt, system_prompt=None, **kw) -> LLMResponse:
        return LLMResponse(content='{"nodes": []}')
    # ... 其余方法按需实现

orch = AgentOrchestrator(llm_backend=FakeLLM(), event_sink=FakeSink(), intent_provider=FakeIntents())
```

## 5. 接口

### 5.1 LLMBackend（`HelixCore/ports/llm.py`）

| 方法 | 签名 | 说明 |
|------|------|------|
| `chat` | `(prompt, system_prompt=None, expect_json=True, temperature=None, top_p=None) -> LLMResponse` | 结构化响应（可含 tool_calls） |
| `simple_chat` | `(prompt, system_prompt=None) -> str` | 一次性纯文本聊天 |
| `ask_json` | `(prompt, system_prompt=None, temperature=None, top_p=None) -> Dict` | 期望 JSON 文档 |
| `ask_with_tools` | `(prompt, system_prompt=None, context_messages=None, emit_stream=True, temperature=None, top_p=None) -> LLMResponse` | 带工具调用支持的对话 |
| `refresh` | `() -> None` | 配置变更后重读配置 |
| `get_provider_model` | `() -> tuple[str, str]` | 返回 `(provider, model)` 供 Token 估算 |

`LLMResponse`：`content: str`、`tool_calls: List[Dict]`、`finish_reason: str`、`usage: Optional[Dict]`（ai_engine `usage` 事件透传）。

### 5.2 EventSink（`HelixCore/ports/events.py`）

| 方法 | 签名 | 说明 |
|------|------|------|
| `emit` | `(request_id, state, graph_nodes=None, node_result=None, completed=False)` | 推送一次状态快照（可附带 DAG 节点图与单节点结果） |
| `cleanup` | `(request_id)` | 释放该请求全部事件缓冲与消费者队列 |

### 5.3 IntentProvider（`HelixCore/ports/intents.py`）

| 方法 | 签名 | 说明 |
|------|------|------|
| `get_registered_intents` | `() -> Dict[str, Any]` | 全部已注册意图（generic 恒排最前） |
| `get_intent_info` | `(intent_type) -> Optional[Dict]` | 单个意图信息；不存在返回 None |
| `register_intent` | `(intent_type, name, description) -> bool` | 注册新意图；generic 禁止 |
| `update_intent` | `(intent_type, data) -> bool` | 合并更新；generic 禁止 |
| `delete_intent` | `(intent_type) -> bool` | 删除意图；generic 禁止 |
| `get_available_intents` | `() -> Dict[str, Any]` | 启用中的意图（generic 恒可用） |
| `get_enabled_intent_ids` | `() -> set` | 启用意图 ID 集合（generic 恒包含） |

### 5.4 BaseTool（`HelixCore/tools/base.py`）

- 类属性：`name: str`、`description: str`、`intents: list`、`parameters: Dict`、`source: str`（自动判定：`MCP` / `内部插件` / `外部插件`）。
- 实例属性：`enabled: bool`（getter/setter）。
- 方法：`execute(**kwargs) -> Any`（抽象，必须实现）、`to_dict() -> Dict`（序列化元数据）、`to_tool_definition() -> Dict`（LLM 工具目录格式）。

### 5.5 ToolRegistry（`HelixCore/tools/base.py`）

单例（`tool_registry` 全局实例），主要方法：

| 方法 | 说明 |
|------|------|
| `register(tool)` / `unregister(name)` | 注册 / 移除工具 |
| `get(name)` / `get_all()` | 按名获取 / 全部工具 |
| `get_all_as_list()` | 序列化列表（`intents` 通配已解析） |
| `get_by_intent(intent)` / `get_enabled_tools()` / `get_intents()` | 按意图 / 启用态 / 全部意图查询 |
| `set_enabled(name, enabled)` | 启停工具 |
| `call_tool(name, arguments=None)` | 执行工具（不存在抛 `ToolNotFoundError`，禁用抛 `ToolDisabledError`） |
| `discover_plugins()` / `initialize()` | 扫描插件目录 / 全量初始化（发现 + 加载启用态） |
| `load_enabled_state()` / `save_enabled_state()` | 从注入的 config store 读写启用态与意图 |
| `set_intent_provider(provider)` / `set_config_store(store)` | 注入 Host 依赖（P4） |

### 5.6 编排配置（`HelixCore/orchestrator/config.py`）

`AgentConfig`（frozen dataclass）：`node_parallel_count`（DAG 并行数，0/1=串行）、`max_graph_updates`（最大图更新次数）、`planning_max_ask_rounds`（规划阶段最大追问轮数）、`max_input_tokens`（规划输入上限）、`planning/execution/finalizer`（各阶段 `SamplingParams(temperature, top_p)`）。`get_graph_sampling(phase)` 按阶段返回采样参数。

### 5.7 任务图（`HelixCore/orchestrator/task_graph.py`）

`TaskGraph` 维护 `TaskNode`（id / 描述 / 依赖 / 结果 / 状态）与 `NodeState` 状态机；提供依赖解析、可执行节点选取、失败路径规避与 `need_update_node` 动态更新支持，是 Phase 2 的执行骨架。

## 6. 兼容性说明

- 重构过程中未改动任何外部契约：SSE 事件格式、RPC method 名、ask_user 协议、`Helix.json` schema、前端 JS 全部保持不变。
- 原 Host 侧直连调用已收敛为三端口注入（`modules/host/` 适配器），旧 `modules/agents/tool_base.py` 等 shim 已删除，全仓无残留引用。

## 7. 测试

```bash
# 单元冒烟（无需启动服务）
python3 test_agent.py
# 端到端（需先启动服务）
python3 Helix.py --rpc-port 11555
# 然后向 POST /api/rpc 发 JSON-RPC 请求，详见 README.md「API 使用」
```
