# Helix AI Agent - 系统设计文档

> **版本**: 1.0  
> **最后更新**: 2026-06-21  
> **项目代号**: Helix  
> **技术栈**: Python 3.12+ / Flask / LangChain / LangGraph / python-pptx / MCP Protocol

---

## 目录

1. [系统概述](#1-系统概述)
2. [架构层设计](#2-架构层设计)
3. [主流程时序图](#3-主流程时序图)
4. [双循环架构 - Todo循环时序图](#4-双循环架构---todo循环时序图)
5. [双循环架构 - 子任务循环时序图](#5-双循环架构---子任务循环时序图)
6. [MCP支持设计](#6-mcp支持设计)
7. [Tool插件化设计](#7-tool插件化设计)
8. [数据模型与状态管理](#8-数据模型与状态管理)
9. [配置管理设计](#9-配置管理设计)
10. [部署与运维](#10-部署与运维)

---

## 1. 系统概述

Helix 是一个混合驱动的 AI Agent 服务，核心理念是**LLM 负责决策，工具负责执行**。系统采用双循环架构（Dual-Loop Architecture），通过 LLM 进行意图识别、任务规划、工具调用判断、数据分析和结果总结，同时支持 MCP（Model Context Protocol）协议实现外部工具的标准化接入。

### 1.1 核心设计原则

| 原则 | 说明 |
|------|------|
| **LLM 驱动决策** | LLM 是系统的"大脑"，负责任务分解、工具选择、结果分析 |
| **双循环编排** | 外层 Todo 循环管理任务进度，内层 Subtask 循环驱动具体执行 |
| **插件化工具** | 所有工具通过 BaseTool 抽象 + ToolRegistry 自动发现 |
| **MCP 标准化** | 外部工具通过 MCP 协议接入，支持 stdio 和 SSE 两种传输模式 |
| **多模型支持** | 统一 LLM 客户端，支持 Ollama / OpenAI / Gemini / DeepSeek |

---

## 2. 架构层设计

系统采用六层架构，各层职责清晰、依赖方向单一向下。

```mermaid
graph TB
    subgraph L1["🌐 接入层 (Access Layer)"]
        A1["Flask REST API<br/>POST /api/agent/router"]
        A2["Admin Web UI<br/>管理控制台"]
        A3["Admin REST API<br/>/api/admin/*"]
    end

    subgraph L2["🧭 路由层 (Routing Layer)"]
        B1["IntentRouter<br/>意图分类 (LLM驱动)"]
        B2["Intent Registry<br/>意图注册表 (配置化)"]
    end

    subgraph L3["🔄 编排层 (Orchestration Layer)"]
        C1["AgentOrchestrator<br/>双循环编排器"]
        C2["TodoManager<br/>任务进度管理"]
        C3["ContextManager<br/>上下文管理"]
        C4["AgentState<br/>LangGraph 状态机"]
    end

    subgraph L4["🔧 工具层 (Tool Layer)"]
        D1["ToolRegistry<br/>插件注册中心"]
        D2["BaseTool 插件<br/>web/image/ppt/code/shell"]
        D3["MCPRegistry<br/>MCP 连接管理"]
        D4["MCPClient<br/>MCP 协议客户端"]
    end

    subgraph L5["🧠 模型层 (LLM Layer)"]
        E1["LLMClient<br/>统一 LLM 接口"]
        E2["Ollama"]
        E3["OpenAI / DeepSeek"]
        E4["Gemini"]
    end

    subgraph L6["⚙️ 基础设施层 (Infrastructure)"]
        F1["ConfigManager<br/>配置管理 (单例)"]
        F2["Logger<br/>日志系统"]
        F3["FileOps<br/>文件操作"]
        F4["Helix.json<br/>配置文件"]
    end

    A1 --> B1
    A2 --> A3
    A3 --> B1
    B1 --> B2
    B1 --> C1
    C1 --> C2
    C1 --> C3
    C1 --> C4
    C1 --> D1
    C1 --> D3
    D1 --> D2
    D3 --> D4
    C1 --> E1
    E1 --> E2
    E1 --> E3
    E1 --> E4
    D2 --> F3
    D4 --> F1
    E1 --> F1
    B1 --> F1
    C1 --> F2
    F1 --> F4

    style L1 fill:#e3f2fd,stroke:#1565c0,color:#000
    style L2 fill:#fff3e0,stroke:#e65100,color:#000
    style L3 fill:#fce4ec,stroke:#c62828,color:#000
    style L4 fill:#e8f5e9,stroke:#2e7d32,color:#000
    style L5 fill:#f3e5f5,stroke:#6a1b9a,color:#000
    style L6 fill:#eceff1,stroke:#37474f,color:#000
```

### 2.1 各层职责

| 层级 | 模块 | 职责 |
|------|------|------|
| **接入层** | `server.py`, `routes.py` | HTTP 端点暴露、请求解析、响应封装、Admin UI |
| **路由层** | `intent_router.py` | LLM 驱动的意图分类，配置化的意图注册与启停 |
| **编排层** | `orchestrator.py`, `todo_manager.py`, `context_manager.py`, `agent_state.py` | 双循环编排、任务状态追踪、上下文构建 |
| **工具层** | `tool_base.py`, `plugins/*`, `mcp_client.py`, `mcp_registry.py` | 插件化工具管理、MCP 协议通信 |
| **模型层** | `llm_client.py` | 多 LLM Provider 统一接口、JSON 模式、Tool Calling |
| **基础设施层** | `config_manager.py`, `logger.py`, `file_ops.py` | 配置读写、日志、文件 IO |

---

## 3. 主流程时序图

描述一个完整请求从用户发起到最终响应返回的全生命周期。

```mermaid
sequenceDiagram
    autonumber
    participant User as 用户/客户端
    participant API as Flask API<br/>(routes.py)
    participant Router as IntentRouter
    participant Orch as Orchestrator
    participant LLM as LLMClient
    participant Tools as Tool Layer<br/>(Plugin + MCP)
    participant Ctx as ContextManager

    User->>API: POST /api/agent/router<br/>{request, intent?}
    API->>API: 生成 request_id
    API->>Orch: process_request(request, id)

    rect rgb(227, 242, 253)
        Note over Orch,LLM: Phase 1: 规划阶段 (Planning)
        Orch->>Ctx: initialize(state)
        Orch->>Ctx: build_llm_context(state)
        Ctx-->>Orch: context string
        Orch->>LLM: decide_json(context + TODO_PLANNING_PROMPT)
        LLM-->>Orch: {intent_type, todos[], thinking}
        Orch->>Orch: todo_manager.set_todos(todos)
        Orch->>Orch: _determine_loop_level()
    end

    rect rgb(252, 228, 236)
        Note over Orch,Tools: Phase 2: Todo 循环 (Loop 1)
        loop 遍历每个 Todo 项
            Orch->>Orch: get_current_todo()
            Note over Orch: Todo[i]: "具体任务描述"

            rect rgb(232, 245, 233)
                Note over Orch,Tools: Subtask 循环 (Loop 2)
                loop LLM 决策循环 (max 20次)
                    Orch->>Ctx: build_subtask_context()
                    Ctx-->>Orch: subtask context
                    Orch->>LLM: with_tools(context, tool_definitions)
                    LLM-->>Orch: {tool_calls?, response?, subtask_complete?}

                    alt 有 tool_calls
                        Orch->>Tools: 执行工具调用
                        Tools-->>Orch: 工具执行结果
                        Orch->>Ctx: add_message(result)
                    else 直接响应
                        Orch->>Orch: 记录 subtask_result
                    end
                end
            end

            Orch->>Orch: todo_manager.advance_todo(result)
        end
    end

    rect rgb(243, 229, 245)
        Note over Orch,LLM: Phase 3: 总结阶段 (Summarization)
        Orch->>Orch: todo_manager.get_completed_summary()
        Orch->>LLM: decide_json(SUMMARIZATION_PROMPT)
        LLM-->>Orch: {summary, generated_files}
        Orch->>Orch: state.final_result = summary
    end

    Orch-->>API: {success, final_result, generated_files, ...}
    API-->>User: JSON Response
```

---

## 4. 双循环架构 - Todo循环时序图

Todo 循环（Loop 1）是外层循环，负责遍历任务清单中的每一项，对每一项调用子任务循环（Loop 2）完成具体工作。

```mermaid
sequenceDiagram
    autonumber
    participant Orch as Orchestrator
    participant TM as TodoManager
    participant State as AgentState
    participant Sub as Subtask Loop<br/>(Loop 2)
    participant Log as Logger

    Orch->>State: orchestrator_phase = "todo_loop"
    Log->>Log: log_section("Phase 2: Todo Loop")
    Orch->>Orch: loop_count = 0

    loop while !todo_manager.is_finished(state)
        Orch->>Orch: loop_count++

        alt loop_count > max_todo_loops (50)
            Orch->>State: error = "Max todo loops exceeded"
            Log->>Log: log_error("Max todo loops exceeded")
            Note over Orch: 退出循环
        end

        Orch->>TM: get_current_todo(state)
        TM->>State: 读取 current_todo_idx, todo_list
        State-->>TM: todo_list[idx]
        TM-->>Orch: current_todo (string)

        alt current_todo is None
            Note over Orch: 退出循环 (无更多任务)
        end

        Log->>Log: log_orchestrator("Todo [i/N]: ...")

        rect rgb(232, 245, 233)
            Note over Orch,Sub: 执行子任务循环
            Orch->>Sub: _subtask_loop(state, current_todo)
            Sub-->>Orch: subtask_result (string)
        end

        Orch->>TM: advance_todo(state, result)
        TM->>State: todos_completed.append({todo, result, status})
        TM->>State: current_todo_idx += 1

        alt current_todo_idx >= len(todo_list)
            TM-->>Orch: True (全部完成)
            Log->>Log: log_orchestrator("All todos completed!")
        else 还有剩余任务
            TM-->>Orch: False (继续)
            TM->>TM: get_current_todo(state) → next_todo
            Log->>Log: log_orchestrator("Moving to next: ...")
        end

        Orch->>TM: get_progress(state)
        TM-->>Orch: 格式化进度字符串
        Log->>Log: log_orchestrator(progress)
    end

    Log->>Log: log_orchestrator("Todo Loop completed.")
```

### 4.1 Todo 循环关键机制

| 机制 | 说明 |
|------|------|
| **最大循环限制** | `max_todo_loops = 50`，防止无限循环 |
| **进度追踪** | `current_todo_idx` 递增，`todos_completed` 记录历史 |
| **状态可视化** | ✅ 已完成 / 🔄 进行中 / ⬜ 待处理 |
| **结果传递** | 每个 Todo 的执行结果传递给下一个 Todo 的上下文 |

---

## 5. 双循环架构 - 子任务循环时序图

子任务循环（Loop 2）是内层循环，由 LLM 驱动决策，决定是调用工具还是直接响应，直到子任务完成或达到最大循环次数。

```mermaid
sequenceDiagram
    autonumber
    participant Orch as Orchestrator
    participant Ctx as ContextManager
    participant LLM as LLMClient
    participant TR as ToolRegistry<br/>(Plugin Tools)
    participant MCP as MCPRegistry<br/>(MCP Tools)
    participant State as AgentState

    Orch->>State: subtask_status = "running"
    Orch->>State: subtask_loop_count = 0

    Note over Orch: 初始化阶段

    rect rgb(255, 243, 224)
        Note over Orch,MCP: 工具定义收集
        Orch->>Orch: 获取 system_prompt (按 intent_type)
        Orch->>TR: get_enabled_tools()
        TR-->>Orch: List[BaseTool]
        Orch->>Orch: 转换为 ToolDefinition[]
        Orch->>MCP: get_tools_for_intent(intent_type)
        MCP-->>Orch: List[MCPTool]
        Orch->>Orch: 合并去重 tool_definitions
    end

    loop while !subtask_complete && loop_count < max_subtask_loops (20)
        Orch->>State: subtask_loop_count++
        Log->>Log: log_orchestrator("Subtask Loop iteration N/20")

        rect rgb(227, 242, 253)
            Note over Orch,LLM: LLM 决策阶段
            Orch->>Ctx: build_subtask_context(state)
            Ctx-->>Orch: subtask context string
            Orch->>LLM: with_tools(context + decision prompt,<br/>tool_definitions, system_prompt)

            alt Provider = Ollama
                Note over LLM: 注入工具描述到 system_prompt<br/>要求 JSON 格式响应
                LLM-->>Orch: JSON {thinking, tool_calls?, response?}
            else Provider = OpenAI/DeepSeek
                Note over LLM: 使用原生 function calling
                LLM-->>Orch: response + tool_calls[]
            end
        end

        Orch->>Orch: _parse_llm_response(content)

        alt 包含 tool_calls
            rect rgb(232, 245, 233)
                Note over Orch,MCP: 工具执行阶段
                loop 遍历每个 tool_call
                    Orch->>Orch: _execute_tool_call(state, tc)

                    alt tool = "web_search"
                        Orch->>MCP: call_tool("web_search", {query})
                        alt MCP 成功
                            MCP-->>Orch: JSON results
                        else MCP 失败
                            Orch->>TR: call_tool("web_search", {query})
                            TR-->>Orch: fallback results
                        end
                        Orch->>TR: call_tool("web_fetch_batch", {urls})
                        TR-->>Orch: fetched content
                        Orch->>State: collected_data.append(content)

                    else tool = "image_search"
                        Orch->>MCP: call_tool("image_search", {query})
                        MCP-->>Orch: image URLs
                        alt intent = "ppt"
                            Orch->>TR: call_tool("image_download", {urls})
                            TR-->>Orch: saved file paths
                            Orch->>State: generated_files.extend(paths)
                        end

                    else 其他工具
                        Orch->>MCP: call_tool(name, args)
                        alt MCP 成功
                            MCP-->>Orch: result text
                        else MCP 失败
                            Orch->>TR: call_tool(name, args)
                            TR-->>Orch: result
                        end
                    end

                    Orch->>Ctx: add_message("assistant", result)
                end
            end

        else 无 tool_calls (直接响应)
            Orch->>Orch: subtask_result = response_data.response
            Log->>Log: log_llm_decision("LLM direct response")
        end

        alt subtask_complete || all_complete
            Log->>Log: log_orchestrator("Subtask completed")
            Note over Orch: subtask_complete = True, 退出循环
        end

        Orch->>Ctx: add_message("assistant", llm_response.content)
    end

    Orch->>State: subtask_history.append(entry)
    Orch->>State: subtask_status = "completed" | "failed"
    Orch-->>Orch: return subtask_result
```

### 5.1 子任务循环关键机制

| 机制 | 说明 |
|------|------|
| **LLM 驱动决策** | 每轮循环由 LLM 决定：调用工具 / 直接响应 / 标记完成 |
| **工具优先级** | MCP 工具优先调用，失败时 fallback 到 Plugin 工具 |
| **自动链式执行** | `web_search` 自动触发 `web_fetch_batch`；`image_search` + PPT 意图自动触发 `image_download` |
| **最大循环限制** | `max_subtask_loops = 20`，防止 LLM 陷入无限决策循环 |
| **上下文累积** | 每轮结果通过 `ContextManager` 追加到对话历史，下一轮 LLM 可见 |

---

## 6. MCP支持设计

MCP（Model Context Protocol）是一种标准化的工具接入协议，Helix 实现了完整的 MCP 客户端，支持 **stdio** 和 **SSE** 两种传输模式。

### 6.1 MCP 整体架构

```mermaid
graph TB
    subgraph Helix["Helix Agent 系统"]
        ORCH["Orchestrator"]
        MCPREG["MCPRegistry<br/>(单例)"]
        CLIENT1["MCPClient<br/>searxng"]
        CLIENT2["MCPClient<br/>image_search"]
        CLIENT3["MCPClient<br/>自定义 MCP Server"]
    end

    subgraph MCP_Servers["MCP Server 进程"]
        S1["searxng_server.py<br/>(stdio transport)"]
        S2["image_search_server.py<br/>(stdio transport)"]
        S3["External MCP Server<br/>(SSE transport)"]
    end

    subgraph Protocol["MCP 协议 (JSON-RPC 2.0)"]
        P1["initialize"]
        P2["tools/list"]
        P3["tools/call"]
        P4["ping"]
    end

    ORCH -->|"get_tools_for_intent()"| MCPREG
    ORCH -->|"call_tool(name, args)"| MCPREG
    MCPREG --> CLIENT1
    MCPREG --> CLIENT2
    MCPREG --> CLIENT3
    CLIENT1 -->|"stdin/stdout"| S1
    CLIENT2 -->|"stdin/stdout"| S2
    CLIENT3 -->|"HTTP SSE"| S3
    CLIENT1 -.-> Protocol
    CLIENT2 -.-> Protocol
    CLIENT3 -.-> Protocol

    style Helix fill:#e3f2fd,stroke:#1565c0,color:#000
    style MCP_Servers fill:#fff3e0,stroke:#e65100,color:#000
    style Protocol fill:#f3e5f5,stroke:#6a1b9a,color:#000
```

### 6.2 MCP 连接生命周期

```mermaid
sequenceDiagram
    autonumber
    participant Reg as MCPRegistry
    participant Client as MCPClient
    participant Server as MCP Server<br/>(子进程/SSE)

    Note over Reg: 初始化阶段 (initialize)
    Reg->>Reg: 读取 Helix.json → mcp_servers 配置
    loop 遍历每个 MCP Server 配置
        alt enabled = true
            Reg->>Client: create_mcp_client(name, config)
            Reg->>Client: connect()

            alt type = "local" (stdio)
                Client->>Server: subprocess.Popen(command, args, env)
                Server-->>Client: 子进程启动
                Note over Client: 启动 stdio_reader 线程
            else type = "server" (SSE)
                Client->>Server: GET /sse (SSE 长连接)
                Server-->>Client: event: endpoint<br/>data: /messages?session_id=xxx
                Note over Client: 启动 SSE listener 线程
            end

            Client->>Server: initialize (JSON-RPC)
            Server-->>Client: {protocolVersion, capabilities, serverInfo}
            Client->>Server: notifications/initialized
            Note over Client: connected = True

            Client->>Server: tools/list (JSON-RPC)
            Server-->>Client: {tools: [{name, description, inputSchema}]}
            Note over Client: 缓存 MCPTool[] 列表
        end
    end

    Note over Reg: 运行阶段 (tool calling)
    Reg->>Client: call_tool("web_search", {query: "..."})
    Client->>Server: tools/call {name, arguments}
    Server-->>Client: {content: [{type: "text", text: "..."}]}
    Client-->>Reg: result text

    Note over Reg: 关闭阶段 (shutdown)
    Reg->>Client: disconnect()
    alt type = "local"
        Client->>Server: process.terminate()
    else type = "server"
        Client->>Client: SSE stop event
        Client->>Server: session.close()
    end
```

### 6.3 MCP 传输模式对比

| 特性 | stdio (local) | SSE (server) |
|------|---------------|--------------|
| **传输方式** | 子进程 stdin/stdout | HTTP SSE + POST |
| **适用场景** | 内置 MCP Server（同机部署） | 外部 MCP Server（远程部署） |
| **进程管理** | Helix 管理子进程生命周期 | 外部独立进程 |
| **通信线程** | stdio_reader 后台线程 | SSE listener 后台线程 |
| **配置方式** | `command` + `args` + `env` | `url` |
| **环境变量** | 通过 `env` 字段注入 | 由外部 Server 自行管理 |

### 6.4 MCP 意图路由

MCPRegistry 通过 `intent_categories` 实现基于意图的工具过滤：

```
MCP Server 配置:
  searxng:
    intent_categories: ["ppt", "research"]
  image_search:
    intent_categories: ["ppt", "research"]

调用时:
  get_tools_for_intent("ppt")     → [web_search, image_search]
  get_tools_for_intent("coding")  → []  (无匹配的 MCP 工具)
  get_tools_for_intent("research") → [web_search, image_search]
```

### 6.5 MCP 协议实现

Helix 实现了 MCP 协议版本 `2024-11-05`，支持以下 JSON-RPC 方法：

| 方法 | 方向 | 说明 |
|------|------|------|
| `initialize` | Client → Server | 握手，交换协议版本和能力 |
| `notifications/initialized` | Client → Server | 初始化完成通知（无响应） |
| `tools/list` | Client → Server | 发现 Server 暴露的工具 |
| `tools/call` | Client → Server | 调用指定工具 |
| `ping` | Client → Server | 心跳检测 |

### 6.6 内置 MCP Server

| Server | 文件 | 工具 | 后端 |
|--------|------|------|------|
| **SearXNG** | `mcp/searxng_server.py` | `web_search` | SearXNG 搜索引擎 API |
| **Image Search** | `mcp/image_search_server.py` | `image_search` | Pexels / Unsplash API |

---

## 7. Tool插件化设计

Helix 的工具系统采用**抽象基类 + 自动发现 + 注册中心**的插件化架构，新增工具只需在 `plugins/` 目录下添加一个 Python 文件。

### 7.1 插件化架构总览

```mermaid
graph TB
    subgraph PluginDir["plugins/ 目录 (自动扫描)"]
        WT["web_tools.py<br/>WebSearchTool<br/>WebFetchBatchTool"]
        IT["image_tools.py<br/>ImageSearchTool<br/>ImageDownloadTool"]
        PT["ppt_tools.py<br/>CreatePPTTool"]
        CT["code_tools.py<br/>SaveCodeTool<br/>RunCodeTool"]
        ST["shell_tools.py<br/>BashTool, ListFilesTool<br/>GrepTool, ReadFileTool<br/>WriteFileTool, DeleteFileTool"]
    end

    subgraph Core["核心框架"]
        BT["BaseTool (ABC)<br/>name, description<br/>category, parameters<br/>execute(**kwargs)"]
        TR["ToolRegistry (单例)<br/>register / unregister<br/>call_tool / get<br/>discover_plugins<br/>enable / disable"]
    end

    subgraph Consumer["消费者"]
        ORCH["Orchestrator<br/>_subtask_loop()"]
        ADMIN["Admin API<br/>/api/admin/plugins"]
    end

    WT -->|subclass| BT
    IT -->|subclass| BT
    PT -->|subclass| BT
    CT -->|subclass| BT
    ST -->|subclass| BT

    BT -->|注册| TR
    TR -->|get_enabled_tools()| ORCH
    TR -->|get_all_as_list()| ADMIN
    TR -->|call_tool(name, args)| ORCH
    TR -->|toggle / enable / disable| ADMIN

    style PluginDir fill:#e8f5e9,stroke:#2e7d32,color:#000
    style Core fill:#e3f2fd,stroke:#1565c0,color:#000
    style Consumer fill:#fff3e0,stroke:#e65100,color:#000
```

### 7.2 插件自动发现流程

```mermaid
sequenceDiagram
    autonumber
    participant Server as server.py
    participant TR as ToolRegistry
    participant FS as plugins/ 目录
    participant Module as Plugin Module

    Server->>TR: tool_registry.initialize()

    rect rgb(232, 245, 233)
        Note over TR,Module: Phase 1: 插件发现 (discover_plugins)
        TR->>FS: os.listdir("plugins/")
        FS-->>TR: ["web_tools.py", "image_tools.py", ...]

        loop 遍历每个 .py 文件 (跳过 _开头)
            TR->>Module: importlib.import_module("plugins.xxx")
            Module-->>TR: module loaded

            loop dir(module) 扫描
                TR->>TR: 检查: issubclass(attr, BaseTool)?<br/>attr is not BaseTool?<br/>hasattr(attr, "name")?
                alt 匹配
                    TR->>Module: attr() → 实例化
                    Module-->>TR: tool instance
                    TR->>TR: register(instance)
                    Note over TR: _tools[name] = instance
                end
            end
        end
    end

    rect rgb(227, 242, 253)
        Note over TR: Phase 2: 加载启停状态 (load_enabled_state)
        TR->>TR: ConfigManager.get("plugins")
        loop 遍历已注册工具
            TR->>TR: tool.enabled = config[name].enabled
        end
    end

    TR-->>Server: 初始化完成<br/>N tools registered
```

### 7.3 BaseTool 抽象基类

```python
class BaseTool(ABC):
    """所有工具插件必须继承的抽象基类"""

    name: str           # 唯一标识符 (如 "web_search")
    description: str    # 人类可读描述 (给 LLM 看)
    category: str       # 分类 (web/image/ppt/code/shell)
    parameters: dict    # JSON Schema 参数定义

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """工具执行入口"""
        pass

    def to_dict(self) -> dict:
        """序列化为 API 响应格式"""

    def to_tool_definition(self) -> dict:
        """转换为 LLM ToolDefinition 格式"""
```

### 7.4 ToolRegistry 核心能力

| 能力 | 方法 | 说明 |
|------|------|------|
| **自动发现** | `discover_plugins()` | 扫描 `plugins/` 目录，导入并注册所有 BaseTool 子类 |
| **注册/注销** | `register(tool)` / `unregister(name)` | 运行时动态管理工具 |
| **查找** | `get(name)` / `get_all()` / `get_by_category(cat)` | 按名称、全部、按分类查找 |
| **启停管理** | `set_enabled(name, bool)` / `get_enabled_tools()` | 运行时启用/禁用工具 |
| **执行** | `call_tool(name, arguments)` | 按名称调用工具，支持异常处理 |
| **持久化** | `load_enabled_state()` / `save_enabled_state()` | 启停状态持久化到 Helix.json |

### 7.5 工具与 MCP 的融合策略

在子任务循环中，Plugin 工具和 MCP 工具被统一为 `ToolDefinition[]` 提供给 LLM：

```mermaid
graph LR
    subgraph ToolDefs["LLM 可见的工具列表"]
        T1["web_search"]
        T2["image_search"]
        T3["create_ppt"]
        T4["web_fetch_batch"]
        T5["save_code"]
        T6["bash"]
        T7["..."]
    end

    subgraph Sources["工具来源"]
        P["Plugin Tools<br/>(ToolRegistry)"]
        M["MCP Tools<br/>(MCPRegistry)"]
    end

    P -->|"get_enabled_tools()"| T3
    P -->|"get_enabled_tools()"| T4
    P -->|"get_enabled_tools()"| T5
    P -->|"get_enabled_tools()"| T6
    P -->|"get_enabled_tools()"| T7
    M -->|"get_tools_for_intent()"| T1
    M -->|"get_tools_for_intent()"| T2

    style ToolDefs fill:#f3e5f5,stroke:#6a1b9a,color:#000
    style Sources fill:#e8f5e9,stroke:#2e7d32,color:#000
```

**执行优先级**: MCP 优先 → Plugin fallback

```
Orchestrator._execute_tool_call(name, args):
  1. 尝试 MCPRegistry.call_tool(name, args)
  2. MCP 失败 → ToolRegistry.call_tool(name, args)
  3. 都失败 → 记录错误到上下文
```

### 7.6 现有工具清单

| 工具名 | 类别 | 来源 | 说明 |
|--------|------|------|------|
| `web_search` | web | Plugin + MCP | 网页搜索 (SearXNG) |
| `web_fetch_batch` | web | Plugin | 批量抓取 URL 内容 |
| `image_search` | image | Plugin + MCP | 图片搜索 (Pexels/Unsplash) |
| `image_download` | image | Plugin | 图片下载到本地 |
| `create_ppt` | ppt | Plugin | PPT 生成 (python-pptx) |
| `save_code` | code | Plugin | 保存代码文件 |
| `run_code` | code | Plugin | 执行 Python 代码 |
| `bash` | shell | Plugin | 执行 Shell 命令 |
| `ls` | shell | Plugin | 列出目录内容 |
| `grep` | shell | Plugin | 文件内容搜索 |
| `read_file` | shell | Plugin | 读取文件内容 |
| `write_file` | shell | Plugin | 写入文件 |
| `delete_file` | shell | Plugin | 删除文件/目录 |

### 7.7 新增工具示例

在 `plugins/` 目录下新建文件即可，无需修改任何注册代码：

```python
# plugins/my_custom_tool.py
from modules.agents.tool_base import BaseTool

class MyCustomTool(BaseTool):
    name = "my_tool"
    description = "描述你的工具功能"
    category = "custom"
    parameters = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "输入参数"}
        },
        "required": ["input"]
    }

    def execute(self, input: str = "", **kwargs):
        # 工具逻辑
        return f"Result for: {input}"
```

重启服务后自动注册。

---

## 8. 数据模型与状态管理

### 8.1 AgentState 状态机

```mermaid
stateDiagram-v2
    [*] --> Planning: process_request()

    Planning --> TodoLoop: 意图识别 + Todo 规划完成

    TodoLoop --> SubtaskLoop: 取当前 Todo
    SubtaskLoop --> ToolExecution: LLM 决策: 调用工具
    SubtaskLoop --> DirectResponse: LLM 决策: 直接响应
    ToolExecution --> SubtaskLoop: 工具结果返回
    DirectResponse --> TodoLoop: 子任务完成, 推进 Todo
    SubtaskLoop --> TodoLoop: 子任务完成 (max loops)

    TodoLoop --> Summarizing: 所有 Todo 完成
    Summarizing --> Done: 总结生成完成

    Done --> [*]: 返回结果

    TodoLoop --> Error: max_todo_loops exceeded
    SubtaskLoop --> Error: max_subtask_loops exceeded
    Error --> [*]: 返回错误
```

### 8.2 AgentState 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_request` | str | 用户原始请求 |
| `intent_type` | str | 意图类型: ppt / research / coding |
| `request_id` | str | 请求唯一标识 |
| `todo_list` | List[str] | 任务清单 |
| `current_todo_idx` | int | 当前执行到的 Todo 索引 |
| `todos_completed` | List[Dict] | 已完成的 Todo 及结果 |
| `current_subtask` | str | 当前正在执行的子任务 |
| `subtask_status` | str | idle / running / completed / failed |
| `subtask_history` | List[Dict] | 子任务执行历史 |
| `subtask_loop_count` | int | 当前子任务循环次数 |
| `collected_data` | List[str] | 收集的数据 |
| `generated_files` | List[str] | 生成的文件路径 |
| `final_result` | str | 最终结果 |
| `orchestrator_phase` | str | planning / todo_loop / subtask_loop / summarizing / done |
| `loop_level` | str | simple / complex |

---

## 9. 配置管理设计

### 9.1 配置架构

`ConfigManager` 采用**单例模式** + **线程安全**，读写 `Helix.json` 配置文件。

```mermaid
graph TB
    subgraph Config["Helix.json"]
        S["server<br/>端口/地址/调试"]
        L["llm<br/>provider/模型参数"]
        T["tools<br/>SearXNG/图片搜索"]
        I["intents<br/>ppt/research/coding"]
        M["mcp_servers<br/>MCP Server 配置"]
        P["plugins<br/>工具启停状态"]
    end

    CM["ConfigManager (单例)"]

    S --> CM
    L --> CM
    T --> CM
    I --> CM
    M --> CM
    P --> CM

    CM -->|"get('llm.provider')"| ORCH["Orchestrator"]
    CM -->|"get('intents')"| IR["IntentRouter"]
    CM -->|"get('mcp_servers')"| MR["MCPRegistry"]
    CM -->|"get('plugins')"| TR["ToolRegistry"]
    CM -->|"get_llm_config()"| LLM["LLMClient"]

    ADMIN["Admin API"] -->|"update_section()"| CM
    CM -->|"_save()"| Config

    style Config fill:#fff3e0,stroke:#e65100,color:#000
    style CM fill:#e3f2fd,stroke:#1565c0,color:#000
```

### 9.2 配置热更新

| 配置变更 | 影响 | 热更新方式 |
|----------|------|------------|
| LLM 参数 | LLMClient | `orchestrator.refresh_llm()` |
| MCP Server | MCPRegistry | `mcp_registry.reload()` |
| 工具启停 | ToolRegistry | `tool_registry.save_enabled_state()` |
| 意图配置 | IntentRouter | 实时读取，无需刷新 |

---

## 10. 部署与运维

### 10.1 服务架构

```mermaid
graph LR
    subgraph Process["server.py 进程"]
        SVC["Service App<br/>(Flask, 主线程)<br/>:11555"]
        ADM["Admin App<br/>(Flask, 子线程)<br/>:11556"]
    end

    subgraph SubProcs["MCP 子进程"]
        SP1["searxng_server.py"]
        SP2["image_search_server.py"]
    end

    subgraph External["外部服务"]
        OLL["Ollama :11434"]
        SX["SearXNG :8080"]
        PX["Pexels API"]
    end

    USER["客户端"] -->|POST /api/agent/router| SVC
    USER -->|浏览器| ADM
    SVC --> SP1
    SVC --> SP2
    SVC --> OLL
    SP1 --> SX
    SP2 --> PX

    style Process fill:#e3f2fd,stroke:#1565c0,color:#000
    style SubProcs fill:#fff3e0,stroke:#e65100,color:#000
    style External fill:#f3e5f5,stroke:#6a1b9a,color:#000
```

### 10.2 日志系统

| 日志类型 | 颜色 | 函数 | 用途 |
|----------|------|------|------|
| Agent → LLM | 蓝色 | `log_agent_to_llm()` | 发送给 LLM 的请求 |
| LLM → Agent | 绿色 | `log_llm_to_agent()` | LLM 返回的响应 |
| Tool 调用 | 青色 | `log_tool_call()` | 工具执行记录 |
| Orchestrator | 黄色 | `log_orchestrator()` | 编排器状态变更 |
| 错误 | 红色 | `log_error()` | 异常信息 |
| 信息 | 白色 | `log_info()` | 一般信息 |

日志同时输出到控制台和 `debugout.log` 文件，Admin UI 提供 Web 日志查看器。

---

> **文档维护**: 本文档随代码迭代同步更新。架构图和时序图使用 Mermaid 语法，可在支持 Mermaid 的 Markdown 渲染器中直接查看。
