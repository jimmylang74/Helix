# AI Hybrid-Driven Agent Service
![Version](https://img.shields.io/github/v/tag/jimmylang74/Helix)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

混合驱动AI Agent服务，基于 Python / Flask / python-pptx / ai_engine 构建。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                    接入层 (Flask API)                 │
│           POST /api/rpc (JSON-RPC 2.0 单入口)         │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              Agent Intent Router                     │
│           意图路由分发 (配置化 + 强制指定)               │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│            Agent Orchestrator (三阶段 DAG)            │
│     ┌─────────────────────────────────────┐         │
│     │ Phase 1: Task Planning              │         │
│     │  LLM 分解请求为 DAG 节点图             │         │
│     │  ┌─────────────────────────────┐    │         │
│     │  │ Phase 2: Node Loop           │    │         │
│     │  │  依赖解析 → 并行/串行执行节点   │    │         │
│     │  │  ┌───────────────────────┐  │    │         │
│     │  │  │ 节点内: LLM决策→Tool   │  │    │         │
│     │  │  └───────────────────────┘  │    │         │
│     │  └─────────────────────────────┘    │         │
│     │ Phase 3: Finalizer (可选汇总)         │         │
│     └─────────────────────────────────────┘         │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              模型层 (LLM via ai_engine)              │
│  Ollama │ OpenAI │ Anthropic │ Gemini │ DeepSeek   │
│  Groq   │ Together │ Mistral │ Custom OpenAI       │
└─────────────────────────────────────────────────────┘
```

## 核心特性

- **三阶段 DAG 架构**: 任务规划(Phase 1) → 节点循环(Phase 2) → 总结(Phase 3)，任务被分解为带依赖关系的 DAG 节点图，无依赖节点可并行执行
- **动态任务图**: 执行中 LLM 可更新节点图（`need_update_node`），失败路径自动标记并规避，支持 `max_graph_updates` 次更新
- **节点级上下文隔离**: 每个节点维护独立的对话历史与工具结果，避免上下文污染；节点间通过 `depends` 依赖传递结果
- **LLM驱动决策**: LLM负责任务分解、工具调用判断、节点完成判定、结果总结
- **配置化意图体系**: 内置 generic 兜底意图，PPT 生成、代码生成等意图在 `Helix.json` 中配置（含规划/节点执行/总结各阶段提示词），可通过 Web 控制台动态增改
- **插件化工具体系**: 内置插件 + 外部插件 + MCP 工具三层架构，支持自动发现与热插拔
- **外部插件扩展**: 在 `plugins/user/` 目录下放入 `.py` 文件即可注册自定义工具，无需修改框架代码
- **多LLM支持**: 通过 [ai_engine](ai_engine/) 子模块统一接入，支持 Ollama / OpenAI / Anthropic / Gemini / DeepSeek / Groq / Together / Mistral 等 10+ 提供商，Web 控制台动态切换
- **MCP工具**: web_search(SearXNG)、image_search(Pexels/Unsplash) 及外部 SSE MCP Server
- **Web管理控制台**: 可视化配置管理、动态 Provider 选择、LLM 交互日志查看、任务 DAG 状态实时可视化（SSE 流）

## 快速开始

### 环境要求

- Python 3.12+
- Ollama / OpenAI / Anthropic / Gemini / DeepSeek (或任意 ai_engine 支持的 Provider)

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行服务

```bash
# 默认配置启动
python3 Helix.py

# 自定义端口
python3 Helix.py --rpc-port 11555 --admin-port 11556

# 调试模式
python3 Helix.py --debug
```

### 访问服务

- **API服务**: `http://localhost:11555/api/rpc`
- **管理控制台**: `http://localhost:11556/`

## API 使用

所有请求通过单一入口 `POST /api/rpc` 分发，`method` 字段决定处理逻辑。

### 发送请求 (自动识别意图)

```bash
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"agent/router","params":{"request":"请搜索2024年AI的发展趋势"}}'
```

### 强制指定意图

```bash
# PPT生成
curl -X POST http://localhost:11555/api/rpc \
  -d '{"jsonrpc":"2.0","id":"2","method":"agent/router","params":{"request":"创建Python入门PPT","intent":"ppt"}}'

# 代码生成
curl -X POST http://localhost:11555/api/rpc \
  -d '{"jsonrpc":"2.0","id":"3","method":"agent/router","params":{"request":"写一个Fibonacci函数","intent":"coding"}}'
```

详细API文档见 [API.md](API.md)

系统设计文档见 [doc/design.md](doc/design.md)（含架构图、时序图、MCP 与插件化设计）

## 配置文件 (`Helix.json`)

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| server.rpc_port | RPC API端口 | 11555 |
| server.admin_port | 管理端口 | 11556 |
| server.node_parallel_count | DAG 节点并行执行数 (0/1=串行) | 1 |
| llm.provider | LLM提供商 (ai_engine provider key) | ollama_native |
| llm.model | 模型名称 | qwen2.5:7b |
| llm.endpoint | API 地址 | http://localhost:11434 |
| llm.api_key | API 密钥 (可选) | (空) |
| llm.verbose | 启用详细日志 | true |
| llm.log_file | LLM 交互日志文件 | llm_engine.log |
| llm.max_input_tokens | 任务规划阶段输入上限 (超限报错) | 32768 |
| llm.max_graph_updates | 执行中任务图最大更新次数 | 5 |


可通过 Web 管理控制台的 LLM 配置页面动态切换 Provider 和填写连接参数，无需手动编辑 JSON。

## 目录结构

```
├── Helix.py                  # 主入口 (Flask 双端口: API + Admin)
├── Helix.json                 # 配置文件 (LLM/MCP/意图/工具)
├── requirements.txt           # Python 依赖
├── README.md                  # 说明文档
├── API.md                     # API 文档
├── debugout.log               # 运行日志输出
├── llm_engine.log             # LLM 引擎交互日志 (ai_engine --verbose --log)
├── test_agent.py              # 测试程序
├── ai_engine/                 # LLM 引擎子模块 (统一多 Provider 接入)
│   ├── ai_engine.py           #   引擎主入口 (run_engine / --get-provider / --output-format events)
│   ├── API-AI-ENGINE.md       #   引擎 API 文档
│   └── example_import.py      #   import 模式使用示例
├── doc/                       # 设计文档
│   └── design.md              #   系统架构设计文档 (Mermaid)
├── HelixCore/                 # 核心引擎包 (与 host 解耦, 依赖注入)
│   ├── interface.py           #   注入端口统一契约 (ABC: LLMBackend/EventSink/LlmEventBus/IntentProvider/LogSink)
│   ├── orchestrator/          #   编排核心
│   │   ├── orchestrator.py    #     三阶段编排器 (规划→节点循环→总结)
│   │   ├── task_graph.py      #     DAG 任务图 (TaskNode/NodeState 状态机)
│   │   ├── agent_state.py     #     请求状态定义 (AgentState TypedDict)
│   │   └── config.py          #     编排配置
│   ├── tools/                 #   工具基座
│   │   └── base.py            #     BaseTool 抽象基类 + ToolRegistry
│   ├── prompts/               #   提示词模板
│   │   └── task_graph_prompts.py #   DAG 三阶段提示词 (规划/节点执行/总结，generic 兜底)
│   └── utils/                 #   工具库
│       └── tokenizer.py       #     Token 估算器
├── modules/                   # host 侧模块 (注入实现)
│   ├── agent/                 #   Agent 层 (意图路由 + 协作设施)
│   │   ├── intent_router.py   #     意图路由 (配置化注册 + 强制指定)
│   │   ├── status_events.py   #     SSE 事件总线 (状态推送/断线回放)
│   │   └── user_question.py   #     用户提问 broker
│   ├── host/                  #   host 适配层 (注入实现)
│   │   ├── ai_engine_backend.py #   LLMBackend 实现 (ai_engine 接入)
│   │   ├── event_sink.py      #     EventSink 实现 (SSE)
│   │   ├── intent_store.py    #     IntentProvider 实现
│   │   ├── llm_event_bus.py   #     LlmEventBus 实现 (llm_events 包装)
│   │   └── tool_context.py    #     工具上下文 (ask_user/cancel)
│   ├── llm/                   #   LLM 层
│   │   └── llm_events.py      #     LLM 事件总线
│   ├── mcp/                   #   MCP 协议层
│   │   ├── mcp_client.py      #     MCP 客户端 (stdio/SSE 双传输)
│   │   └── mcp_registry.py    #     MCP 注册中心 (生命周期/意图路由)
│   ├── app/                   #   应用层
│   │   └── routes.py          #     Flask 路由 (JSON-RPC API + Admin + Web UI)
│   ├── config/                #   配置管理
│   │   └── config_manager.py  #     配置管理器 (单例/线程安全)
│   └── utils/                 #   工具库
│       ├── logger.py          #     日志系统 (彩色/双输出)
│       └── file_ops.py        #     文件操作
├── plugins/                   # 工具插件 (自动发现, 继承 BaseTool)
│   ├── web_tools.py           #   Web 工具 (web_search, web_fetch_batch)
│   ├── image_tools.py         #   图片工具 (image_search, image_download)
│   ├── ppt_tools.py           #   PPT 工具 (create_ppt)
│   ├── code_tools.py          #   代码工具 (save_code, run_code)
│   ├── shell_tools.py         #   Shell 工具 (bash, ls, grep, read/write/delete_file)
│   └── user/                  #   外部插件 (用户自定义, 来源标记为 "外部插件")
│       ├── __init__.py
│       ├── plugin.md          #     插件编写指南
│       ├── weather_tool.py    #     示例: 天气查询工具
│       └── calculator_tool.py #     示例: 安全计算器工具
├── mcp/                       # MCP Server 实现 (stdio 传输)
│   ├── searxng_server.py      #   SearXNG 搜索 MCP Server
│   └── image_search_server.py #   图片搜索 MCP Server (Pexels/Unsplash)
├── web/                       # 前端 (Admin 管理控制台)
│   ├── templates/             #   HTML 模板
│   │   ├── base.html          #     基础布局
│   │   ├── dashboard.html     #     仪表盘
│   │   ├── quick_test.html    #     快速测试
│   │   ├── config.html        #     配置管理
│   │   ├── logs.html          #     日志查看
│   │   └── history.html       #     请求历史
│   ├── static/                #   静态资源
│   │   ├── css/style.css      #     样式
│   │   └── js/                #     JavaScript
│   │       ├── main.js        #       主逻辑
│   │       ├── dashboard.js   #       仪表盘
│   │       ├── quick_test.js  #       快速测试
│   │       ├── config.js      #       配置管理
│   │       ├── logs.js        #       日志查看
│   │       ├── history.js     #       请求历史
│   │       └── i18n.js        #       国际化
│   └── locales/               #   国际化语言文件
│       ├── zh-CN.json         #     中文
│       └── en.json            #     英文
├── db/                        # 数据库 (预留)
├── download/                  # 下载文件 (图片等)
└── output/                    # 输出文件 (PPT/代码)
```

## 测试

```bash
# 运行测试程序 (需要先启动Helix.py)
python3 test_agent.py
```

## 日志

- 同时输出到屏幕和控制台文件 `debugout.log`
- 颜色标识: 蓝色(Agent→LLM), 绿色(LLM→Agent), 青色(Tool调用), 黄色(Orchestrator状态)
- 管理控制台提供Web日志查看器

## 外部插件开发

在 `plugins/user/` 目录下放入 `.py` 文件即可自动注册为工具，来源标记为 `"外部插件"`，与内置插件区分。

**快速上手**：继承 `BaseTool`，实现 `execute()` 方法，定义 `name`/`description`/`intents`/`parameters`，重启服务即可生效。

```python
from HelixCore.tools.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "工具说明，LLM 根据此描述决定是否调用"
    intents = ["generic"]  # 绑定意图，["*"] 表示所有意图可用
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "参数说明"}},
        "required": ["query"]
    }

    def execute(self, query: str = "", **kwargs) -> str:
        return f"结果: {query}"
```

完整指南和更多示例见 [plugins/user/plugin.md](plugins/user/plugin.md)。

## 版权
MIT
