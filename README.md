# AI Hybrid-Driven Agent Service
![Version](https://img.shields.io/github/v/tag/jimmylang74/Helix)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

[中文](README.md) | [English](README_EN.md)

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
- **多通道接入**: 每个通道（Web 快速测试 / 微信 iLinkBot）持有独立的私有 agent 运行时（编排器 + 工具注册表 + 提问 broker），LLM 只能触达所在通道的工具；通道三件套工具（`ask_user` / `get_context` / `clear_context`）按通道适配落点
- **定时任务系统**: Helix 自维护调度器（独立于系统 crond），支持 daily/weekly/monthly 触发与 system（Shell 命令）/ agent（智能体执行）两类任务；任务定义存于 `db/cron.json`（手工编辑自动热重载），运行结果写入 `db/cron.db`（SQLite）；Web 控制台可视化管理，LLM 可经全局共享的 cron 工具自主增删改查任务
- **外部插件扩展**: 在 `plugins/user/` 目录下放入 `.py` 文件即可注册自定义工具，无需修改框架代码
- **多LLM支持**: 通过 [ai_engine](ai_engine/) 子模块统一接入，支持 Ollama / OpenAI / Anthropic / Gemini / DeepSeek / Groq / Together / Mistral 等 10+ 提供商，Web 控制台动态切换
- **MCP工具**: web_search(SearXNG)、image_search(Pexels/Unsplash) 及外部 SSE MCP Server
- **Web管理控制台**: 可视化配置管理、动态 Provider 选择、LLM 交互日志查看、任务 DAG 状态实时可视化（SSE 流）

## 作者开发环境

- **操作系统**: Ubuntu 24.04
- **LLM 运行时**: Ollama + qwen3.5:27b
- **GPU**: NVIDIA V100 16G × 2

## 快速开始

### 拉取代码

项目包含 git submodule（`ai_engine`），请使用 `--recursive` 参数拉取：

```bash
git clone --recursive https://github.com/jimmylang74/Helix.git
```

若已用普通 `git clone` 拉取，可执行以下命令补拉 submodule：

```bash
git submodule update --init --recursive
```

### 环境要求

- Python 3.12+
- Ollama / OpenAI / Anthropic / Gemini / DeepSeek (或任意 ai_engine 支持的 Provider)

### MCP 工具外部依赖

内建的两个 MCP Server（[mcp/searxng_server.py](mcp/searxng_server.py)、[mcp/image_search_server.py](mcp/image_search_server.py)）额外依赖以下外部环境条件，配置项位于 `Helix.json` 的 `mcp_servers.<name>.env`：

| MCP Server | 工具 | 外部依赖 | 配置项 |
|------------|------|---------|--------|
| `mcp/searxng_server.py` | `web_search` | 一个**可访问的 SearXNG 实例**，且**必须启用 JSON 输出格式**（其 `settings.yml` 的 `search.formats` 需包含 `json`，官方默认仅 `html`）；本服务需能出站访问该实例 | `SEARXNG_BASE_URL`（代码默认 `http://localhost:8888`）、`SEARXNG_MAX_RESULTS` |
| `mcp/image_search_server.py` | `image_search` | Pexels 与/或 Unsplash 的 **API Key**；本服务需能出站访问 `api.pexels.com` / `api.unsplash.com` | `IMAGE_PROVIDER`（`pexels`/`unsplash`）、`PEXELS_API_KEY`、`UNSPLASH_API_KEY` |

注意：

- `web_search` 客户端固定请求 JSON 结果（POST `format=json` 并解析 `results` 字段，无 HTML 回退）。实例未启用 JSON 格式时 SearXNG 返回 HTTP 403，工具将报 "Search error: HTTP 403"。
- 未配置 Pexels/Unsplash API Key 时 `image_search` **不会报错**，而是静默降级返回占位图（mock）结果。

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

## 定时任务

Helix 内置自维护的定时任务调度器（区别于操作系统 crond），服务启动后自动开始调度，也可在 Web 控制台「配置管理 → 定时任务」中启停。

- **任务定义**: `db/cron.json`（JSON 数组，可直接手工编辑；调度器每 tick 自动感知文件变化并热重载）
- **运行结果**: `db/cron.db`（SQLite，逐次记录 stdout/stderr 与耗时，控制台可查看输出）
- **补漏策略**: 不回补——停摆期间错过的时点直接跳过，只计算下一次触发

### 任务字段（db/cron.json）

| 字段 | 说明 |
|------|------|
| title | 任务名称（必填） |
| time | 触发时间 HH:MM，24 小时制（必填） |
| repeat | daily / weekly / monthly（必填） |
| weekday | weekly 时必填：0=周一 … 6=周日 |
| day_of_month | monthly 时必填：1-31（超出当月天数时取当月最后一天） |
| task_type | system=执行 Shell 命令 / agent=交给智能体执行（必填） |
| description | system=Shell 命令内容；agent=自然语言任务描述（必填） |
| enabled | 是否启用（默认 true） |

### RPC 接口与智能体工具

通过 `POST /api/rpc` 调用：`cron.list` / `cron.create` / `cron.update` / `cron.delete` / `cron.results` / `cron.status` / `cron.start` / `cron.stop`。

智能体侧提供 7 个全局共享工具（任何通道可用）：`list_cron` / `create_cron` / `modify_cron` / `delete_cron` / `start_cron` / `stop_cron` / `cron_status`。Cron 通道本身不注册 ask_user 等三件套工具，规划提示词中的提问环节会自动裁剪。

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
├── README.md                  # 说明文档 (中文)
├── README_EN.md               # 说明文档 (英文)
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
│   ├── channels/              #   多通道框架 (每通道私有 agent 运行时)
│   │   ├── base.py            #     ChannelAdapter 抽象基类 (含 ask_user/get_context/clear_context 落点契约)
│   │   ├── manager.py         #     ChannelManager (通道注册/生命周期/跨通道应答与取消)
│   │   ├── runtime.py         #     build_channel_runtime: 私有编排器+工具注册表+三件套绑定
│   │   ├── routes.py          #     imbot/* RPC 与 /api/imbot-stream SSE
│   │   ├── events.py          #     通道消息广播/订阅 (SSE)
│   │   ├── store.py           #     通道消息与会话上下文持久化 (SQLite)
│   │   ├── web/               #     Web 快速测试通道 (channel/event_sink/history_store)
│   │   └── cron/              #     定时任务模块 (Helix 自维护调度)
│   │       ├── store.py       #       任务定义 (db/cron.json) + 运行结果 (db/cron.db SQLite)
│   │       ├── scheduler.py   #       CronScheduler 调度线程 (tick 扫描/mtime 热重载/不回补)
│   │       └── channel.py     #       CronChannel 适配器 (私有 agent 运行时, 不注册三件套工具)
│   ├── host/                  #   host 适配层 (注入实现)
│   │   ├── ai_engine_backend.py #   LLMBackend 实现 (ai_engine 接入)
│   │   ├── config_builder.py  #     编排配置构建
│   │   ├── intent_store.py    #     IntentProvider 实现
│   │   ├── llm_event_bus.py   #     LlmEventBus 实现 (llm_events 包装)
│   │   ├── log_sink.py        #     LogSink 实现 (日志注入 HelixCore)
│   │   └── plugin_loader.py   #     插件发现与启停配置持久化
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
│   ├── mcp_tools.py           #   MCP 工具适配 (MCPToolAdapter, 单独注册不自动扫描)
│   ├── cron_tools.py          #   定时任务工具 (7 个全局共享: list/create/modify/delete/start/stop/status)
│   └── user/                  #   外部插件 (用户自定义, 来源标记为 "外部插件")
│       ├── __init__.py
│       ├── plugin.md          #     插件编写指南
│       ├── weather_tool.py    #     示例: 天气查询工具
│       └── calculator_tool.py #     示例: 安全计算器工具
├── imChannels/                # IM 通道适配器 (ChannelAdapter 实现)
│   └── wechat/                #   微信 iLinkBot 通道
│       ├── channel.py         #     轮询/收发/agent 分发/通道工具落点
│       ├── authenticator.py   #     QR 码登录与会话恢复
│       ├── ilink_client.py    #     iLink API HTTP 客户端
│       └── README.md          #     微信通道详细文档
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
├── db/                        # 数据库与数据文件 (定时任务定义 cron.json / 运行结果 cron.db 等)
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
