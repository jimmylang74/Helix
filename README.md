# AI Hybrid-Driven Agent Service
![Version](https://img.shields.io/github/v/tag/jimmylang74/Helix)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

混合驱动AI Agent服务，基于 LangChain + LangGraph + python-pptx + ai_engine 构建。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                    接入层 (Flask REST API)            │
│              POST /api/agent/router                  │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              Agent Intent Router                     │
│           意图路由分发 (LLM分类)                       │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│            Agent Orchestrator                        │
│     ┌─────────────────────────────────────┐         │
│     │  Loop 1: Todo List (总体任务循环)    │         │
│     │     ┌─────────────────────────┐    │         │
│     │     │ Loop 2: Subtask (子任务)  │    │         │
│     │     │  分解 → 独立执行 → 摘要   │    │         │
│     │     │  ┌───────────────────┐  │    │         │
│     │     │  │ Loop 3: 工具执行   │  │    │         │
│     │     │  │  LLM决策 → Tool   │  │    │         │
│     │     │  └───────────────────┘  │    │         │
│     │     └─────────────────────────┘    │         │
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

- **三循环架构**: 总体任务循环(Todo Loop) → 子任务分解(Subtask Loop) → 工具执行(Iteration Loop)
- **分层上下文管理**: Todo摘要 → Subtask摘要 → 对话历史，逐层传递避免上下文污染
- **LLM驱动决策**: LLM负责意图识别、任务分解、工具调用判断、数据分析、结果总结
- **三种预置Agent**: PPT生成、智能搜索、代码生成
- **多LLM支持**: 通过 [ai_engine](ai_engine/) 子模块统一接入，支持 Ollama / OpenAI / Anthropic / Gemini / DeepSeek / Groq / Together / Mistral 等 10+ 提供商，Web 控制台动态切换
- **MCP工具**: web_search(SearXNG)、image_search(Pexels/Unsplash)
- **Web管理控制台**: 可视化配置管理、动态 Provider 选择、LLM 交互日志查看

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
python3 Helix.py --port 11555 --admin-port 11556

# 调试模式
python3 Helix.py --debug
```

### 访问服务

- **API服务**: `http://localhost:11555/api/agent/router`
- **管理控制台**: `http://localhost:11556/`

## API 使用

### 发送请求 (自动识别意图)

```bash
curl -X POST http://localhost:11555/api/agent/router \
  -H "Content-Type: application/json" \
  -d '{"request": "请搜索2024年AI的发展趋势"}'
```

### 强制指定意图

```bash
# PPT生成
curl -X POST http://localhost:11555/api/agent/router \
  -d '{"request": "创建Python入门PPT", "intent": "ppt"}'

# 代码生成
curl -X POST http://localhost:11555/api/agent/router \
  -d '{"request": "写一个Fibonacci函数", "intent": "coding"}'
```

详细API文档见 [API.md](API.md)

系统设计文档见 [doc/design.md](doc/design.md)（含架构图、时序图、MCP 与插件化设计）

## 配置文件 (`Helix.json`)

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| server.service_port | 服务端口 | 11555 |
| server.admin_port | 管理端口 | 11556 |
| llm.provider | LLM提供商 (ai_engine provider key) | ollama_native |
| llm.model | 模型名称 | qwen2.5:7b |
| llm.endpoint | API 地址 | http://localhost:11434 |
| llm.api_key | API 密钥 (可选) | (空) |
| llm.verbose | 启用详细日志 | true |
| llm.log_file | LLM 交互日志文件 | llm_engine.log |


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
├── modules/                   # 核心模块
│   ├── core/                  #   核心编排
│   │   ├── agent_state.py     #     LangGraph 状态定义 (AgentState)
│   │   ├── orchestrator.py    #     三循环编排器 (Todo→Subtask分解→工具执行)
│   │   ├── context_manager.py #     分层上下文管理 (todo/subtask/conversation)
│   │   └── todo_manager.py    #     任务清单管理 (进度追踪)
│   ├── llm/                   #   LLM 层
│   │   └── llm_client.py      #     统一 LLM 客户端 (通过 ai_engine 接入所有 Provider)
│   ├── agents/                #   Agent 层
│   │   ├── intent_router.py   #     意图路由 (LLM分类 + 配置化注册)
│   │   ├── agent_tools.py     #     Agent 工具集 (兼容层)
│   │   └── tool_base.py       #     BaseTool 抽象基类 + ToolRegistry
│   ├── mcp/                   #   MCP 协议层
│   │   ├── mcp_client.py      #     MCP 客户端 (stdio/SSE 双传输)
│   │   └── mcp_registry.py    #     MCP 注册中心 (生命周期/意图路由)
│   ├── app/                   #   应用层
│   │   └── routes.py          #     Flask 路由 (API + Admin + Web UI)
│   ├── prompts/               #   提示词模板
│   │   ├── system_prompts.py  #     系统级提示词 (编排/规划/总结)
│   │   ├── ppt_prompts.py     #     PPT 生成提示词
│   │   ├── search_prompts.py  #     搜索研究提示词
│   │   └── coding_prompts.py  #     代码生成提示词
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
│   └── shell_tools.py         #   Shell 工具 (bash, ls, grep, read/write/delete_file)
├── mcp/                       # MCP Server 实现 (stdio 传输)
│   ├── searxng_server.py      #   SearXNG 搜索 MCP Server
│   └── image_search_server.py #   图片搜索 MCP Server (Pexels/Unsplash)
├── web/                       # 前端 (Admin 管理控制台)
│   ├── templates/             #   HTML 模板
│   │   ├── base.html          #     基础布局
│   │   ├── dashboard.html     #     仪表盘
│   │   ├── config.html        #     配置管理
│   │   ├── logs.html          #     日志查看
│   │   └── history.html       #     请求历史
│   ├── static/                #   静态资源
│   │   ├── css/style.css      #     样式
│   │   └── js/                #     JavaScript
│   │       ├── main.js        #       主逻辑
│   │       ├── dashboard.js   #       仪表盘
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

## 版权
MIT
