# AI Hybrid-Driven Agent Service

混合驱动AI Agent服务，基于 LangChain + LangGraph + python-pptx + Ollama 构建。

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
│     │     │     LLM决策 → Tool执行   │    │         │
│     │     └─────────────────────────┘    │         │
│     └─────────────────────────────────────┘         │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              模型层 (LLM)                             │
│    Ollama │ OpenAI │ Gemini │ DeepSeek              │
└─────────────────────────────────────────────────────┘
```

## 核心特性

- **双循环架构**: 总体任务循环(Todo Loop) + 子任务循环(Subtask Loop)
- **LLM驱动决策**: LLM负责意图识别、任务规划、工具调用判断、数据分析、结果总结
- **三种预置Agent**: PPT生成、智能搜索、代码生成
- **多LLM支持**: Ollama(默认)、OpenAI、Gemini、DeepSeek 可配置切换
- **MCP工具**: web_search(SearXNG)、image_search(Pexels/Unsplash)
- **Web管理控制台**: 可视化配置管理、日志查看

## 快速开始

### 环境要求

- Python 3.12+
- Ollama (或其他LLM后端)

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行服务

```bash
# 默认配置启动
python3 server.py

# 自定义端口
python3 server.py --port 11555 --admin-port 11556

# 调试模式
python3 server.py --debug
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

## 配置文件 (`ai_agent.json`)

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| server.service_port | 服务端口 | 11555 |
| server.admin_port | 管理端口 | 11556 |
| llm.provider | LLM提供商 | ollama |
| llm.ollama.model | Ollama模型 | qwen2.5:7b |
| tools.searxng.enabled | 启用SearXNG | false |
| tools.image_search.provider | 图片搜索 | pexels |

## 目录结构

```
├── server.py              # 主入口
├── ai_agent.json           # 配置文件
├── requirements.txt        # 依赖
├── README.md               # 说明文档
├── API.md                  # API文档
├── debugout.log            # 日志输出
├── test_agent.py           # 测试程序
├── modules/
│   ├── core/               # 核心编排
│   │   ├── agent_state.py      # LangGraph状态定义
│   │   ├── orchestrator.py     # 双循环编排器
│   │   ├── context_manager.py  # 上下文管理
│   │   └── todo_manager.py     # 任务管理
│   ├── llm/                # LLM层
│   │   └── llm_client.py       # 统一LLM客户端
│   ├── agents/             # Agent层
│   │   ├── intent_router.py    # 意图路由
│   │   └── agent_tools.py      # Agent工具集
│   ├── prompts/            # 提示词
│   ├── config/             # 配置管理
│   └── utils/              # 工具
├── web/                    # 前端
│   ├── templates/          # HTML模板
│   └── static/             # JS/CSS
├── download/               # 下载文件
└── output/                 # 输出文件
```

## 测试

```bash
# 运行测试程序 (需要先启动server.py)
python3 test_agent.py
```

## 日志

- 同时输出到屏幕和控制台文件 `debugout.log`
- 颜色标识: 蓝色(Agent→LLM), 绿色(LLM→Agent), 青色(Tool调用), 黄色(Orchestrator状态)
- 管理控制台提供Web日志查看器

## 版权
MIT
