# WebSearch MCP Server (extracted from oh-my-openagent plugin)

自 oh-my-openagent 插件抠出的 **websearch** MCP，供 Helix 本地直接使用。

## 出处 (Provenance)

插件的 websearch MCP 并非自研服务，而是一份**远程 MCP 配置**（源码见
`oh-my-openagent` 插件 `packages/omo-opencode/src/mcp/websearch.ts`）：

| Provider | 远程 MCP 端点 | 认证 |
|----------|---------------|------|
| Exa (默认) | `https://mcp.exa.ai/mcp?tools=web_search_exa` | 可选 `Authorization: Bearer $EXA_API_KEY`（无 Key 走匿名档，经验证可用） |
| Tavily | `https://mcp.tavily.com/mcp/` | 必填 `Authorization: Bearer $TAVILY_API_KEY` |

本目录将其"抠出"为一个 Helix 可直接 spawn 的 **stdio MCP Server**：
一个 **stdio ↔ Streamable HTTP 代理** —— 每个来自 Helix 的 JSON-RPC 消息
原样转发到远端 Provider 端点（复用同一 session），并把 Provider 的响应
（SSE / 普通 JSON）原样回传。工具面（`web_search_exa` 等）完全由 Provider
定义，与插件行为一致。

> 为什么是代理而不是直连 REST？Exa 的远程 **MCP 端点**匿名可用（已实测），
> 而其 **REST API**（`api.exa.ai/search`）匿名会返回 HTTP 402 Payment required。
> 代理方案与插件完全相同，因此保留了匿名免费档。

## Helix 接入

在 `Helix.json` 的 `mcp_servers` 增加（`type: "local"`，stdio 直连）：

```json
"websearch": {
  "type": "local",
  "enabled": true,
  "command": "python3",
  "args": ["mcp/websearch/websearch_mcp.py"],
  "env": {
    "WEBSEARCH_PROVIDER": "exa",
    "EXA_API_KEY": ""
  }
}
```

> - 换成 Tavily：`"WEBSEARCH_PROVIDER": "tavily"` 并填入 `TAVILY_API_KEY`。
> - 需要代理时在 `env` 中加 `"HTTPS_PROXY"` / `"HTTP_PROXY"`（requests 自动生效）。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `WEBSEARCH_PROVIDER` | `exa` | `exa` 或 `tavily` |
| `EXA_API_KEY` | 空 | Exa API Key（可选，匿名档可用） |
| `TAVILY_API_KEY` | 空 | Tavily API Key（provider=tavily 时必填） |

## 验证

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"web_search_exa","arguments":{"query":"MCP Model Context Protocol","num_results":3}}}\n' \
  | python3 websearch_mcp.py
```

启动日志见 stderr（如 `provider=exa endpoint=...`）。