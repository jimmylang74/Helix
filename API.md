# AI Agent Service API Documentation

## Overview

AI混合驱动智能服务的RESTful API文档。服务提供三种核心Agent能力：PPT生成、智能搜索和代码生成。

**Base URL**: `http://<host>:<service_port>/api`  
**Admin URL**: `http://<host>:<admin_port>/api/admin`  
**默认端口**: 服务端口 11555, 管理端口 11556

---

## 1. Agent API

### 1.1 路由请求

向Agent发送请求，自动识别意图并处理。

```
POST /api/agent/router
```

**Request Body**:
```json
{
    "request": "用户请求内容",
    "intent": "auto",        // 可选: auto, ppt, research, coding
    "stream": false          // 是否流式响应（预留）
}
```

**Response** (200 OK):
```json
{
    "success": true,
    "request_id": "req_abc123def456",
    "intent_type": "research",
    "final_result": "处理结果摘要...",
    "generated_files": [
        "output/presentation_20241201_120000.pptx"
    ],
    "todos_completed": 3,
    "subtask_loops": 7
}
```

**Error Response** (4xx/5xx):
```json
{
    "success": false,
    "request_id": "req_abc123def456",
    "error": "错误描述信息"
}
```

### 1.2 查询请求状态

获取某个请求的当前处理状态。

```
GET /api/agent/status/<request_id>
```

**Response**:
```json
{
    "success": true,
    "request_id": "req_abc123def456",
    "intent_type": "research",
    "orchestrator_phase": "todo_loop",
    "todo_progress": "✅ [1/3] 搜索信息\n🔄 [2/3] 分析数据\n⬜ [3/3] 生成回答",
    "current_todo": "搜索和分析数据",
    "subtask_status": "running",
    "generated_files": [],
    "error": null
}
```

---

## 2. Admin API

### 2.1 获取配置

```
GET /api/admin/config
```

**Response**:
```json
{
    "success": true,
    "config": {
        "server": {
            "service_port": 11555,
            "admin_port": 11556,
            "host": "0.0.0.0",
            "debug": true
        },
        "llm": {
            "provider": "ollama_native",
            "model": "qwen2.5:7b",
            "endpoint": "http://localhost:11434",
            "api_key": "",
            "verbose": true,
            "log_file": "llm_engine.log"
        },
        "tools": { ... },
        "intents": { ... }
    }
}
```

### 2.2 更新配置

```
POST /api/admin/config
```

**Request Body** (方式1 - 更新整个section):
```json
{
    "section": "llm",
    "values": {
        "provider": "ollama_native",
        "model": "qwen2.5:7b",
        "endpoint": "http://localhost:11434",
        "api_key": "",
        "verbose": true,
        "log_file": "llm_engine.log"
    }
}
```

**Request Body** (方式2 - 更新单个字段):
```json
{
    "settings": {
        "llm.model": "llama3.2:3b",
        "tools.searxng.enabled": true
    }
}
```

### 2.3 测试LLM连接

```
POST /api/admin/llm/test
```

**Response**:
```json
{
    "success": true,
    "response": "OK. I am working correctly.",
    "provider": "ollama_native"
}
```

### 2.4 获取可用 LLM Provider 列表

```
GET /api/admin/llm/providers
```

从 `ai_engine --get-provider` 获取当前支持的所有 Provider 信息。

**Response**:
```json
{
    "success": true,
    "providers": {
        "ollama_native": {
            "name": "Ollama Chat API  (/api/chat)",
            "description": "Ollama Chat API  (/api/chat)",
            "default_endpoint": "http://localhost:11434"
        },
        "openai": {
            "name": "OpenAI API",
            "description": "OpenAI API",
            "default_endpoint": "https://api.openai.com/v1"
        },
        "...": "..."
    }
}
```

### 2.5 获取 LLM 交互日志

```
GET /api/admin/llm/logs?lines=200
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| lines | int | 200 | 返回最近的行数 |

日志文件由 `Helix.json` 中 `llm.log_file` 配置，需在 LLM 配置中启用 `verbose: true`。

**Response**:
```json
{
    "success": true,
    "logs": ["[2026-07-12] [INFO] provider=ollama_native model=qwen2.5:7b\n", "..."],
    "total_lines": 512
}
```

### 2.6 获取日志

```
GET /api/admin/logs?lines=200
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| lines | int | 200 | 返回最近的行数 |
| file | string | debugout.log | 日志文件名 |

**Response**:
```json
{
    "success": true,
    "logs": ["[2024-12-01] [INFO] ...", "..."],
    "total_lines": 1024,
    "file": "debugout.log"
}
```

### 2.7 意图管理

#### 获取所有意图
```
GET /api/admin/intents
```

#### 注册/更新意图
```
POST /api/admin/intents/<intent_type>
```
```json
{
    "enabled": true,
    "name": "翻译",
    "description": "根据用户要求进行翻译"
}
```

#### 删除意图
```
DELETE /api/admin/intents/<intent_type>
```

---

## 3. Web UI

管理控制台可通过浏览器访问：
- **Dashboard**: `http://<host>:<admin_port>/`
- **配置管理**: `http://<host>:<admin_port>/config`
- **运行日志**: `http://<host>:<admin_port>/logs`
- **使用记录**: `http://<host>:<admin_port>/history`

---

## 4. 错误码

| HTTP状态码 | 说明 |
|-----------|------|
| 200 | 请求成功 |
| 400 | 请求参数错误 |
| 404 | 资源未找到 |
| 500 | 服务端内部错误 |

---

## 5. 使用示例

### curl 示例

```bash
# 发送Agent请求（自动识别）
curl -X POST http://localhost:11555/api/agent/router \
  -H "Content-Type: application/json" \
  -d '{"request": "请搜索2024年AI发展趋势", "intent": "auto"}'

# 强制PPT生成
curl -X POST http://localhost:11555/api/agent/router \
  -H "Content-Type: application/json" \
  -d '{"request": "创建关于Python入门的PPT", "intent": "ppt"}'

# 查询请求状态
curl http://localhost:11555/api/agent/status/req_abc123

# 获取配置
curl http://localhost:11556/api/admin/config

# 更新LLM配置
curl -X POST http://localhost:11556/api/admin/config \
  -H "Content-Type: application/json" \
  -d '{"section": "llm", "values": {"provider": "openai", "model": "gpt-4o", "endpoint": "https://api.openai.com/v1", "api_key": "sk-xxx"}}'

# 测试LLM连接
curl -X POST http://localhost:11556/api/admin/llm/test

# 获取可用 Provider 列表
curl http://localhost:11556/api/admin/llm/providers

# 获取 LLM 交互日志
curl "http://localhost:11556/api/admin/llm/logs?lines=100"

# 获取系统日志
curl "http://localhost:11556/api/admin/logs?lines=100"
```

### Python 示例

```python
import requests

# 发送请求
resp = requests.post(
    "http://localhost:11555/api/agent/router",
    json={"request": "搜索Python FastAPI教程", "intent": "research"}
)
result = resp.json()
print(result["final_result"])

# 获取状态
status = requests.get(f"http://localhost:11555/api/agent/status/{result['request_id']}")
print(status.json())
```
