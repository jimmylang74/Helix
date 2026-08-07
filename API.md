# AI Agent Service API Documentation

## Overview

AI混合驱动智能服务的JSON-RPC 2.0 API文档。服务按配置的意图（`Helix.json → intents`）提供 Agent 能力：通用任务、PPT 生成、代码生成等，意图及其各阶段提示词可在配置或 Web 控制台中管理。

**Base URL**: `http://<host>:<rpc_port>`
**RPC Endpoint**: `POST /api/rpc` (single entry point, method dispatch)
**Admin URL**: `http://<host>:<admin_port>`
**默认端口**: RPC API端口 11555, 管理端口 11556

---

## 1. JSON-RPC 2.0 Protocol

All API calls go through a single endpoint:

```
POST /api/rpc
```

**Request**:
```json
{
    "jsonrpc": "2.0",
    "id": "<unique-id>",
    "method": "<method-name>",
    "params": { ... }
}
```

**Success Response**:
```json
{
    "jsonrpc": "2.0",
    "id": "<unique-id>",
    "result": { ... }
}
```

**Error Response**:
```json
{
    "jsonrpc": "2.0",
    "id": "<unique-id>",
    "error": {
        "code": -32601,
        "message": "Method 'xxx' not found"
    }
}
```

---

## 2. Available Methods

### 2.1 Agent

#### `agent/router`

向Agent发送请求，自动识别意图并处理。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| request | string | ✅ | 用户请求内容 |
| intent | string | | auto / 任意配置中注册的意图 ID，如 generic / ppt / coding (默认 auto) |
| rpc_id | string | | 前端追踪ID（如 `rpc_xxx`），后端自动映射为内部 `req_id`，便于前端跨页面导航恢复状态 |
| stream | bool | | 是否流式返回 (默认 false) |

**Result**:
```json
{
    "success": true,
    "request_id": "req_abc123def456",
    "intent_type": "generic",
    "final_result": "处理结果摘要...",
    "generated_files": ["output/presentation_20241201_120000.pptx"],
    "todos_completed": 3,
    "subtask_loops": 7
}
```

#### `agent/status`

获取某个请求的当前处理状态。支持 `request_id` 或 `rpc_id` 查询（后端自动识别并转换）。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| request_id | string | | 内部请求ID（`req_xxx` 格式） |
| rpc_id | string | | 前端追踪ID（`rpc_xxx` 格式），优先级高于 `request_id` |

**Result**:
```json
{
    "request_id": "req_abc123def456",
    "intent_type": "generic",
    "orchestrator_phase": "todo_loop",
    "todo_progress": "...",
    "current_todo": "搜索和分析数据",
    "subtask_status": "running"
}
```

---

### 2.2 Config

#### `config.get`

获取完整配置。

**Params**: (none)

**Result**:
```json
{
    "config": {
        "server": { "rpc_port": 11555, "admin_port": 11556, "host": "0.0.0.0" },
        "llm": { "provider": "ollama_native", "model": "qwen2.5:7b", ... },
        "tools": { "...": "..." },
        "intents": { "...": "..." }
    }
}
```

#### `config.update`

更新配置。

**Params** (方式1 - 更新整个section):
```json
{
    "section": "llm",
    "values": {
        "provider": "openai",
        "model": "gpt-4o",
        "endpoint": "https://api.openai.com/v1"
    }
}
```

**Params** (方式2 - 更新单个字段):
```json
{
    "settings": {
        "llm.model": "llama3.2:3b"
    }
}
```

---

### 2.3 Intents

#### `intents.get`

获取所有已注册的意图。

**Params**: (none)

#### `intents.update`

注册或更新一个意图。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| intent_type | string | ✅ | 意图ID |
| enabled | bool | | 是否启用 |
| name | string | | 显示名称 |
| description | string | | 描述 |

#### `intents.delete`

删除一个意图。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| intent_type | string | ✅ | 意图ID |

---

### 2.4 LLM

#### `llm.test`

测试LLM连接。

**Params**: (none)

**Result**:
```json
{
    "response": "OK. I am working correctly.",
    "provider": "ollama_native"
}
```

#### `llm.providers`

获取可用LLM Provider列表 (从 ai_engine --get-provider 获取)。

**Params**: (none)

#### `llm.logs`

获取LLM交互日志。

**Params**:
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| lines | int | 200 | 返回最近的行数 |

---

### 2.5 Logs / History

#### `logs.get`

获取系统运行日志。

**Params**:
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| file | string | debugout.log | 日志文件名 |
| lines | int | 200 | 返回最近的行数 |

#### `history.get`

获取请求历史。

**Params**: (none)

---

### 2.6 MCP

#### `mcp.servers`

获取所有MCP Server配置及状态。

**Params**: (none)

#### `mcp.servers.save`

创建或更新MCP Server配置。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | ✅ | Server名称 |
| type | string | | server / local |
| command | string | | 本地模式命令 |
| args | array | | 命令参数 |
| url | string | | 远程模式URL |
| enabled | bool | | 是否启用 |
| env | object | | 环境变量 |

#### `mcp.servers.delete`

删除MCP Server配置。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | ✅ | Server名称 |

#### `mcp.test`

测试MCP连接。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | | Server名称 (默认 "test-server") |
| config | object | ✅ | Server配置 |

#### `mcp.tools`

获取MCP工具列表 (可按意图过滤)。

**Params**:
| 参数 | 类型 | 说明 |
|------|------|------|
| intent | string | 按意图过滤 (留空返回全部) |

#### `mcp.reload`

重新加载所有MCP连接。

**Params**: (none)

---

### 2.7 Plugins

#### `plugins.get`

获取所有已注册的插件工具。

**Params**: (none)

#### `plugins.toggle`

启用/禁用插件。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| tool_name | string | ✅ | 工具名称 |
| enabled | bool | | 启用/禁用 (留空则翻转) |

#### `plugins.intents`

保存插件的意图分配。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| tool_name | string | ✅ | 工具名称 |
| intents | array | | 意图列表 |

#### `plugins.detail`

获取插件详情。

**Params**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| tool_name | string | ✅ | 工具名称 |

---

## 3. Web UI

管理控制台可通过浏览器访问：
- **Dashboard**: `http://<host>:<admin_port>/`
- **配置管理**: `http://<host>:<admin_port>/config`
- **运行日志**: `http://<host>:<admin_port>/logs`
- **使用记录**: `http://<host>:<admin_port>/history`

---

## 4. JSON-RPC 2.0 错误码

| 错误码 | 说明 |
|--------|------|
| -32700 | Parse error |
| -32600 | Invalid request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |

---

## 5. 使用示例

### curl 示例

```bash
# 发送Agent请求（自动识别）
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"agent/router","params":{"request":"请搜索2024年AI发展趋势","intent":"auto","rpc_id":"rpc_abc123"}}'

# 强制PPT生成
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"2","method":"agent/router","params":{"request":"创建关于Python入门的PPT","intent":"ppt","rpc_id":"rpc_xyz789"}}'

# 查询请求状态（支持 request_id 或 rpc_id）
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"3","method":"agent/status","params":{"request_id":"req_abc123"}}'

# 获取配置
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"4","method":"config.get","params":{}}'

# 更新LLM配置
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"5","method":"config.update","params":{"section":"llm","values":{"provider":"openai","model":"gpt-4o","endpoint":"https://api.openai.com/v1","api_key":"sk-xxx"}}}'

# 测试LLM连接
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"6","method":"llm.test","params":{}}'

# 获取可用 Provider 列表
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"7","method":"llm.providers","params":{}}'

# 获取 LLM 交互日志
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"8","method":"llm.logs","params":{"lines":100}}'

# 获取系统日志
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"9","method":"logs.get","params":{"lines":100}}'
```

### Python 示例

```python
import requests

RPC_URL = "http://localhost:11555/api/rpc"

def rpc_call(method, params=None, req_id="1"):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        payload["params"] = params
    resp = requests.post(RPC_URL, json=payload)
    return resp.json()

# 发送请求
result = rpc_call("agent/router", {
    "request": "搜索Python FastAPI教程",
    "intent": "generic",
    "rpc_id": "rpc_py001"
})
print(result["result"]["final_result"])

# 获取状态（支持 request_id 或 rpc_id）
status = rpc_call("agent/status", {
    "rpc_id": "rpc_py001"
})
print(status)
```
