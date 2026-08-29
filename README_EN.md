# AI Hybrid-Driven Agent Service
![Version](https://img.shields.io/github/v/tag/jimmylang74/Helix)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

[English](README_EN.md) | [中文](README.md)

A hybrid-driven AI Agent service built on Python / Flask / python-pptx / ai_engine.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│               Access Layer (Flask API)               │
│          POST /api/rpc (JSON-RPC 2.0 entry)          │
└───────────────────────────┬──────────────────────────┘
                            │                           
┌───────────────────────────▼──────────────────────────┐
│                 Agent Intent Router                  │
│        Intent routing (config-based + forced)        │
└───────────────────────────┬──────────────────────────┘
                            │                           
┌───────────────────────────▼──────────────────────────┐
│           Agent Orchestrator (3-phase DAG)           │
│     ┌───────────────────────────────────────────┐    │
│     │          Phase 1: Task Planning           │    │
│     │      LLM decomposes the request into      │    │
│     │             a DAG node graph              │    │
│     │  ┌───────────────────────────────┐        │    │
│     │  │      Phase 2: Node Loop       │        │    │
│     │  │   Dependency resolution ->    │        │    │
│     │  │   parallel/serial execution   │        │    │
│     │  │  ┌───────────────────────┐    │        │    │
│     │  │  │ Node: LLM decision -> │    │        │    │
│     │  │  │         Tool          │    │        │    │
│     │  │  └───────────────────────┘    │        │    │
│     │  └───────────────────────────────┘        │    │
│     │       Phase 3: Finalizer (optional)       │    │
│     └───────────────────────────────────────────┘    │
└───────────────────────────┬──────────────────────────┘
                            │                           
┌───────────────────────────▼──────────────────────────┐
│           Model Layer (LLM via ai_engine)            │
│   Ollama │ OpenAI │ Anthropic │ Gemini │ DeepSeek    │
│     Groq   │ Together │ Mistral │ Custom OpenAI      │
└──────────────────────────────────────────────────────┘
```

## Core Features

- **Three-phase DAG architecture**: Task planning (Phase 1) → node loop (Phase 2) → summarization (Phase 3). Requests are decomposed into a DAG of nodes with dependencies; independent nodes can run in parallel.
- **Dynamic task graph**: The LLM can update the node graph during execution (`need_update_node`); failed paths are automatically marked and avoided, with up to `max_graph_updates` updates.
- **Node-level context isolation**: Each node keeps its own conversation history and tool results to avoid context pollution; results are passed between nodes via `depends` dependencies.
- **LLM-driven decisions**: The LLM handles task decomposition, tool-call decisions, node completion checks, and final summarization.
- **Configurable intent system**: Built-in generic fallback intent; intents such as PPT generation and code generation are configured in `Helix.json` (including planning/node-execution/summarization prompts per phase) and can be dynamically added or changed via the Web console.
- **Plugin-based tool system**: Three-layer architecture of built-in plugins + external plugins + MCP tools, with auto-discovery and hot-swap support.
- **Multi-channel access**: Each channel (Web quick test / WeChat iLinkBot) owns a private agent runtime (orchestrator + tool registry + question broker); the LLM can only reach tools registered in its own channel. The channel tool trio (`ask_user` / `get_context` / `clear_context`) is adapted per channel.
- **Scheduled tasks (cron)**: A Helix-managed scheduler (independent of the OS crond) supporting daily/weekly/monthly triggers with two task types — system (shell command) and agent (handled by the agent). Task definitions live in `db/cron.json` (manual edits are hot-reloaded); run results are written to `db/cron.db` (SQLite). Manageable visually from the Web console, and the LLM can manage tasks itself via globally shared cron tools.
- **External plugin extension**: Drop a `.py` file into `plugins/user/` to register a custom tool without modifying the framework code.
- **Multi-LLM support**: Unified access through the [ai_engine](ai_engine/) submodule, supporting 10+ providers (Ollama / OpenAI / Anthropic / Gemini / DeepSeek / Groq / Together / Mistral, etc.), switchable dynamically from the Web console.
- **MCP tools**: web_search (SearXNG), image_search (Pexels/Unsplash), and external SSE MCP servers.
- **Web admin console**: Visual configuration management, dynamic provider selection, LLM interaction log viewer, and real-time task DAG visualization (SSE stream).

## Author Development Environment

- **OS**: Ubuntu 24.04
- **LLM runtime**: Ollama + qwen3.5:27b
- **GPU**: NVIDIA V100 16G × 2

## Quick Start

### Clone the Repository

The project contains a git submodule (`ai_engine`), so clone with `--recursive`:

```bash
git clone --recursive https://github.com/jimmylang74/Helix.git
```

If you already cloned without `--recursive`, pull the submodule with:

```bash
git submodule update --init --recursive
```

### Requirements

- Python 3.12+
- Ollama / OpenAI / Anthropic / Gemini / DeepSeek (or any provider supported by ai_engine)

### External Dependencies for MCP Tools

The two built-in MCP servers ([mcp/searxng_server.py](mcp/searxng_server.py), [mcp/image_search_server.py](mcp/image_search_server.py)) rely on the following external environment conditions, configured under `mcp_servers.<name>.env` in `Helix.json`:

| MCP Server | Tool | External Dependency | Config Keys |
|------------|------|--------------------|-------------|
| `mcp/searxng_server.py` | `web_search` | An **accessible SearXNG instance** with **JSON output format enabled** (`search.formats` in its `settings.yml` must include `json`; the official default only has `html`); this service must be able to reach the instance outbound | `SEARXNG_BASE_URL` (default `http://localhost:8888`), `SEARXNG_MAX_RESULTS` |
| `mcp/image_search_server.py` | `image_search` | An **API Key** for Pexels and/or Unsplash; this service must be able to reach `api.pexels.com` / `api.unsplash.com` outbound | `IMAGE_PROVIDER` (`pexels`/`unsplash`), `PEXELS_API_KEY`, `UNSPLASH_API_KEY` |

Notes:

- The `web_search` client always requests JSON results (POSTs `format=json` and parses the `results` field; no HTML fallback). If the instance does not have JSON enabled, SearXNG returns HTTP 403 and the tool reports "Search error: HTTP 403".
- If no Pexels/Unsplash API Key is configured, `image_search` does **not** fail; it silently falls back to placeholder (mock) images.

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run the Service

```bash
# Start with default config
python3 Helix.py

# Custom ports
python3 Helix.py --rpc-port 11555 --admin-port 11556

# Debug mode
python3 Helix.py --debug
```

### Access the Service

- **API**: `http://localhost:11555/api/rpc`
- **Admin console**: `http://localhost:11556/`

## API Usage

All requests are dispatched through the single entry point `POST /api/rpc`; the `method` field determines the handling logic.

### Send a Request (auto intent detection)

```bash
curl -X POST http://localhost:11555/api/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"agent/router","params":{"request":"Search for AI trends in 2024"}}'
```

### Force a Specific Intent

```bash
# PPT generation
curl -X POST http://localhost:11555/api/rpc \
  -d '{"jsonrpc":"2.0","id":"2","method":"agent/router","params":{"request":"Create an intro-to-Python PPT","intent":"ppt"}}'

# Code generation
curl -X POST http://localhost:11555/api/rpc \
  -d '{"jsonrpc":"2.0","id":"3","method":"agent/router","params":{"request":"Write a Fibonacci function","intent":"coding"}}'
```

Full API documentation: [API.md](design/API.md)

System design document: [design/design.md](design/design.md) (in Chinese; includes architecture diagrams, sequence diagrams, MCP & plugin design)

## Scheduled Tasks

Helix ships a self-managed task scheduler (separate from the operating system's crond). Scheduling starts automatically with the service and can be toggled from the Web console under Config → Scheduled Tasks.

- **Task definitions**: `db/cron.json` (a plain JSON array you can edit by hand; the scheduler detects file changes every tick and hot-reloads)
- **Run results**: `db/cron.db` (SQLite; each run appends stdout/stderr and duration, viewable from the console)
- **Missed runs**: never replayed — schedules missed while stopped are skipped and only the next future occurrence is computed

### Task Fields (db/cron.json)

| Field | Description |
|-------|-------------|
| title | Task name (required) |
| time | Trigger time HH:MM, 24-hour (required) |
| repeat | daily / weekly / monthly (required) |
| weekday | Required for weekly: 0=Monday … 6=Sunday |
| day_of_month | Required for monthly: 1-31 (clamped to the month's last day) |
| task_type | system = run a shell command / agent = handled by the agent (required) |
| description | system = shell command; agent = natural-language task description (required) |
| enabled | Whether the task is active (default true) |

### RPC API & Agent Tools

Call via `POST /api/rpc`: `cron.list` / `cron.create` / `cron.update` / `cron.delete` / `cron.results` / `cron.status` / `cron.start` / `cron.stop`.

Seven globally shared tools are available to the agent on any channel: `list_cron` / `create_cron` / `modify_cron` / `delete_cron` / `start_cron` / `stop_cron` / `cron_status`. The cron channel itself does not register the ask_user tool trio; planning prompts automatically trim their question sections accordingly.

## Configuration File (`Helix.json`)

| Key | Description | Default |
|-----|-------------|---------|
| server.rpc_port | RPC API port | 11555 |
| server.admin_port | Admin port | 11556 |
| server.node_parallel_count | Number of DAG nodes executed in parallel (0/1 = serial) | 1 |
| llm.provider | LLM provider (ai_engine provider key) | ollama_native |
| llm.model | Model name | qwen2.5:7b |
| llm.endpoint | API endpoint | http://localhost:11434 |
| llm.api_key | API key (optional) | (empty) |
| llm.verbose | Enable verbose logging | true |
| llm.log_file | LLM interaction log file | llm_engine.log |
| llm.max_input_tokens | Input limit for the task planning phase (error if exceeded) | 32768 |
| llm.max_graph_updates | Maximum task graph updates during execution | 5 |

You can dynamically switch providers and fill in connection parameters from the LLM config page of the Web admin console — no need to edit the JSON manually.

## Directory Structure

```
├── Helix.py                  # Main entry (Flask dual ports: API + Admin)
├── Helix.json                # Configuration file (LLM/MCP/intents/tools)
├── requirements.txt          # Python dependencies
├── README.md                 # Documentation (Chinese)
├── README_EN.md              # Documentation (English)
├── debugout.log              # Runtime log output
├── llm_engine.log            # LLM engine interaction log (ai_engine --verbose --log)
├── test_agent.py             # Test program
├── ai_engine/                # LLM engine submodule (unified multi-provider access)
│   ├── ai_engine.py          #   Engine main entry (run_engine / --get-provider / --output-format events)
│   ├── API-AI-ENGINE.md      #   Engine API documentation
│   └── example_import.py     #   Import-mode usage example
├── design/                   # Design documents
│   ├── API.md                #   API documentation
│   └── design.md             #   System architecture design document (Mermaid)
├── HelixCore/                # Core engine package (decoupled from host, dependency injection)
│   ├── interface.py          #   Injection port contracts (ABC: LLMBackend/EventSink/LlmEventBus/IntentProvider/LogSink)
│   ├── orchestrator/         #   Orchestration core
│   │   ├── orchestrator.py   #     Three-phase orchestrator (planning → node loop → finalization)
│   │   ├── task_graph.py     #     DAG task graph (TaskNode/NodeState state machine)
│   │   ├── agent_state.py    #     Request state definitions (AgentState TypedDict)
│   │   └── config.py         #     Orchestration config
│   ├── tools/                #   Tool foundation
│   │   └── base.py           #     BaseTool abstract base class + ToolRegistry
│   ├── prompts/              #   Prompt templates
│   │   └── task_graph_prompts.py #   DAG three-phase prompts (planning/node execution/finalization, generic fallback)
│   └── utils/                #   Utilities
│       └── tokenizer.py      #     Token estimator
├── modules/                  # Host-side modules (injected implementations)
│   ├── agent/                #   Agent layer (intent routing + collaboration facilities)
│   │   ├── intent_router.py  #     Intent router (config-based registration + forced)
│   │   ├── status_events.py  #     SSE event bus (status push / disconnect replay)
│   │   └── user_question.py  #     User question broker
│   ├── channels/             #   Multi-channel framework (per-channel private agent runtime)
│   │   ├── base.py           #     ChannelAdapter ABC (incl. ask_user/get_context/clear_context contracts)
│   │   ├── manager.py        #     ChannelManager (registration/lifecycle/cross-channel answer & cancel)
│   │   ├── runtime.py        #     build_channel_runtime: private orchestrator+tool registry+tool trio binding
│   │   ├── dispatcher.py     #     OutputDispatcher: cross-channel output registry (cron result → WeChat, etc.)
│   │   ├── routes.py         #     imbot/* RPC and /api/imbot-stream SSE
│   │   ├── events.py         #     Channel message broadcast/subscribe (SSE)
│   │   ├── store.py          #     Channel message and session-context persistence (SQLite)
│   │   ├── web/              #     Web quick-test channel (channel/event_sink/history_store)
│   │   └── cron/             #     Scheduled-task module (Helix-managed scheduling)
│   │       ├── store.py      #       Task definitions (db/cron.json) + run results (db/cron.db SQLite)
│   │       ├── scheduler.py  #       CronScheduler thread (tick scanning / mtime hot-reload / no catch-up)
│   │       └── channel.py    #       CronChannel adapter (private agent runtime, no tool trio registered)
│   ├── host/                 #   Host adapter layer (injected implementations)
│   │   ├── ai_engine_backend.py #   LLMBackend implementation (ai_engine integration)
│   │   ├── config_builder.py #     Orchestration config builder
│   │   ├── intent_store.py   #     IntentProvider implementation
│   │   ├── llm_event_bus.py  #     LlmEventBus implementation (llm_events wrapper)
│   │   ├── log_sink.py       #     LogSink implementation (logging injected into HelixCore)
│   │   └── plugin_loader.py  #     Plugin discovery and enable-state persistence
│   ├── llm/                  #   LLM layer
│   │   └── llm_events.py     #     LLM event bus
│   ├── mcp/                  #   MCP protocol layer
│   │   ├── mcp_client.py     #     MCP client (stdio/SSE dual transports)
│   │   └── mcp_registry.py   #     MCP registry (lifecycle/intent routing)
│   ├── app/                  #   Application layer
│   │   └── routes.py         #     Flask routes (JSON-RPC API + Admin + Web UI)
│   ├── config/               #   Configuration management
│   │   └── config_manager.py #     Config manager (singleton/thread-safe)
│   └── utils/                #   Utilities
│       ├── logger.py         #     Logging system (colored/dual output)
│       └── file_ops.py       #     File operations
├── plugins/                  # Tool plugins (auto-discovered, inherit BaseTool)
│   ├── web_tools.py          #   Web tools (web_search, web_fetch_batch)
│   ├── image_tools.py        #   Image tools (image_search, image_download)
│   ├── ppt_tools.py          #   PPT tools (create_ppt)
│   ├── code_tools.py         #   Code tools (save_code, run_code)
│   ├── shell_tools.py        #   Shell tools (bash, ls, grep, read/write/delete_file)
│   ├── mcp_tools.py          #   MCP tool adapter (MCPToolAdapter; registered separately, not auto-scanned)
│   ├── cron_tools.py         #   Cron tools (7 globally shared: list/create/modify/delete/start/stop/status)
│   └── user/                 #   External plugins (user-defined, marked as "external plugin")
│       ├── __init__.py
│       ├── plugin.md         #     Plugin authoring guide
│       ├── weather_tool.py   #     Example: weather query tool
│       └── calculator_tool.py #    Example: safe calculator tool
├── imChannels/               # IM channel adapters (ChannelAdapter implementations)
│   └── wechat/               #   WeChat iLinkBot channel
│       ├── channel.py        #     Polling / messaging / agent dispatch / channel tool trio
│       ├── authenticator.py  #     QR-code login and session restore
│       ├── ilink_client.py   #     iLink API HTTP client
│       └── README.md         #     WeChat channel documentation
├── mcp/                      # MCP server implementations (stdio transport)
│   ├── searxng_server.py     #   SearXNG search MCP server
│   └── image_search_server.py #   Image search MCP server (Pexels/Unsplash)
├── web/                      # Frontend (Admin console)
│   ├── templates/            #   HTML templates
│   │   ├── base.html         #     Base layout
│   │   ├── dashboard.html    #     Dashboard
│   │   ├── quick_test.html   #     Quick test
│   │   ├── config.html       #     Config management
│   │   ├── logs.html         #     Log viewer
│   │   └── history.html      #     Request history
│   ├── static/               #   Static assets
│   │   ├── css/style.css     #     Styles
│   │   └── js/               #     JavaScript
│   │       ├── main.js       #       Main logic
│   │       ├── dashboard.js  #       Dashboard
│   │       ├── quick_test.js #       Quick test
│   │       ├── config.js     #       Config management
│   │       ├── logs.js       #       Log viewer
│   │       ├── history.js    #       Request history
│   │       └── i18n.js       #       Internationalization
│   └── locales/              #   i18n locale files
│       ├── zh-CN.json        #     Chinese
│       └── en.json           #     English
├── db/                       # Databases & data files (scheduled-task defs/results, channel storage, etc.)
├── download/                 # Downloaded files (images, etc.)
└── output/                   # Output files (PPT/code)
```

## Testing

```bash
# Run the test program (requires Helix.py to be started first)
python3 test_agent.py
```

## Logging

- Output to both the screen and the console file `debugout.log`
- Color coding: blue (Agent→LLM), green (LLM→Agent), cyan (Tool calls), yellow (Orchestrator status)
- The admin console provides a web log viewer

## External Plugin Development

Drop a `.py` file into `plugins/user/` and it will be auto-registered as a tool, marked as "external plugin" to distinguish it from built-in plugins.

**Quick start**: inherit `BaseTool`, implement `execute()`, and define `name`/`description`/`intents`/`parameters`, then restart the service.

```python
from HelixCore.tools.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "Tool description; the LLM decides whether to call it based on this"
    intents = ["generic"]  # Bound intent; ["*"] means available to all intents
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Parameter description"}},
        "required": ["query"]
    }

    def execute(self, query: str = "", **kwargs) -> str:
        return f"Result: {query}"
```

Full guide and more examples: [plugins/user/plugin.md](plugins/user/plugin.md) (in Chinese)

## License
MIT
