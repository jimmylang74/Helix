"""
JSON-RPC 2.0 API routes for the AI Agent Service.
Single entry point: POST /api/rpc  (method dispatch)
Web UI routes served by the admin app.
"""

import json
import os
import sys
import uuid
import threading
from datetime import datetime
from typing import Any
from flask import Blueprint, request, jsonify, render_template, send_from_directory, Response
from modules.llm.llm_events import stream as llm_event_stream
from modules.agent import status_events
from modules.utils import log_watcher
from modules.channels.web import history_store
from modules.utils.paths import PROJECT_ROOT, project_path

from HelixCore.tools.base import tool_registry as global_tool_registry
from modules.host.plugin_loader import save_tool_config
from modules.config.config_manager import ConfigManager
from modules.host.intent_store import intent_store
from modules.mcp.mcp_registry import registry as mcp_registry
from modules.mcp.mcp_client import create_mcp_client
from modules.mcp import mcp_events
from modules.utils.logger import log_info, log_error, log_debug
from modules.channels.cron import store as cron_store
from modules.channels.cron.scheduler import get_scheduler
from modules.channels.dispatcher import get_dispatcher


# 运行时注入：ChannelManager 由组合根（Helix.py）装配后经 configure() 注入，
# RPC agent/* 经其路由到 Web 通道私有运行时，用户应答/取消跨通道分发
_channel_manager: Any = None


def configure(channel_manager):
    """组合根注入 ChannelManager 实例。"""
    global _channel_manager
    _channel_manager = channel_manager
    mcp_registry.on_server_state_change = _on_mcp_state_change
    mcp_registry.start_reconnect_monitor()


def _web_runtime():
    """Web 快速测试通道的私有运行时。"""
    ch = _channel_manager.get("web")
    if ch is None or ch.runtime is None:
        raise RuntimeError("Web channel runtime not configured")
    return ch.runtime


def _sync_mcp_tools() -> None:
    """Re-register MCP tools into the shared pool and every live channel registry."""
    from plugins.mcp_tools import register_mcp_tools
    register_mcp_tools(global_tool_registry)
    if _channel_manager is not None:
        _channel_manager.refresh_mcp_tools()


def _on_mcp_state_change(name: str, connected: bool, tools_count: int):
    """React to MCP state transitions detected by the reconnect monitor."""
    try:
        _sync_mcp_tools()
    except Exception as e:
        log_error(f"MCP state change handler failed: {e}")
    mcp_events.broadcast({
        "type": "update",
        "name": name,
        "connected": connected,
        "tools_count": tools_count,
    })


# Blueprints
api_bp = Blueprint("api", __name__, url_prefix="/api")
admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


# ============================================================
# JSON-RPC 2.0 Error Codes (per spec)
# ============================================================
PARSE_ERROR      = -32700
INVALID_REQUEST  = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS   = -32602
INTERNAL_ERROR   = -32603


# ============================================================
# JSON-RPC 2.0 Helpers
# ============================================================

def _rpc_id() -> str | None:
    """Extract JSON-RPC request id from the incoming body."""
    try:
        data = request.get_json(force=True, silent=True)
        if data and "id" in data:
            return data["id"]
    except Exception:
        pass
    return uuid.uuid4().hex[:12]


def _rpc_success(result, rpc_id: str | None = None) -> tuple:
    """JSON-RPC 2.0 success envelope."""
    return jsonify({
        "jsonrpc": "2.0",
        "id": rpc_id or _rpc_id(),
        "result": result,
    })


def _rpc_error(code: int, message: str, rpc_id: str | None = None) -> tuple:
    """JSON-RPC 2.0 error envelope."""
    return jsonify({
        "jsonrpc": "2.0",
        "id": rpc_id or _rpc_id(),
        "error": {"code": code, "message": message},
    })


# ============================================================
# Internal handler functions
# ------------------------------------------------------------
# Each receives params dict (already extracted from request body).
# All return a plain dict (result payload for _rpc_success).
# Exceptions propagate to _dispatch which wraps them as RPC errors.
# ============================================================

# ---- Agent ----

def _agent_router(params):
    if not params:
        raise ValueError("Missing 'request' field in params")

    # 用户回答等待中的 ask_user 问题（ask_user 工具调用会阻塞节点执行，直到这里交付回答）
    request_id = params.get("request_id", "")
    if request_id:
        answer = params.get("answer", "")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Missing 'answer' field in params")
        if not _channel_manager.deliver_answer(request_id, answer):
            raise ValueError(f"Request '{request_id}' is not waiting for a user answer")
        log_info(f"[{request_id}] user answered: {answer[:100]}")
        return {
            "success": True,
            "request_id": request_id,
            "status": "answered",
        }

    if "request" not in params:
        raise ValueError("Missing 'request' field in params")
    user_request = params["request"]
    forced_intent = params.get("intent", "auto")
    if forced_intent != "auto":
        registered = _web_runtime().intent_provider.get_registered_intents()
        if forced_intent not in registered:
            raise ValueError(
                f"Invalid intent: {forced_intent}. Must be one of: auto, "
                + ", ".join(sorted(registered))
            )

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    log_info(f"[{request_id}] intent={forced_intent}, request={user_request[:100]}...")

    def _run():
        created_at = datetime.now().isoformat(timespec="seconds")
        result = None
        try:
            result = _web_runtime().orchestrator.process_request(user_request, request_id, forced_intent=forced_intent)
        finally:
            # Host 侧收尾：请求结束（成功/失败/取消/异常）即唤醒可能仍阻塞的 ask_user
            _web_runtime().broker.cancel(request_id)
            # 记录使用记录（成功/失败/取消全覆盖），供"使用记录"页面展示
            if result is not None:
                history_store.record({
                    "request_id": request_id,
                    "user_request": user_request,
                    "intent_type": result.get("intent_type", ""),
                    "success": not result.get("error") and not result.get("cancelled"),
                    "created_at": created_at,
                    "error": result.get("error"),
                    "generated_files": result.get("generated_files", []),
                    "final_answer": result.get("final_result", ""),
                    "session_id": history_store.get_current_session_id(),
                })

    threading.Thread(target=_run, daemon=True).start()

    return {
        "success": True,
        "request_id": request_id,
        "intent_type": forced_intent if forced_intent != "auto" else "",
        "status": "started",
    }


def _agent_status(params):
    request_id = params.get("request_id", "")
    if not request_id:
        raise ValueError("Missing 'request_id' in params")
    state = _web_runtime().orchestrator.get_state(request_id)
    if not state:
        raise ValueError(f"Request '{request_id}' not found")
    return state


def _agent_cancel(params):
    request_id = params.get("request_id", "")
    if not request_id:
        raise ValueError("Missing 'request_id' in params")

    cancelled = _channel_manager.cancel_request(request_id)

    if not cancelled:
        raise ValueError(f"Request '{request_id}' not found or already completed")

    return {"success": True, "message": "Request cancelled"}


# ---- Config ----

def _config_get(params):
    config = ConfigManager()
    return {"config": config.get_all()}


def _config_update(params):
    config = ConfigManager()
    section = params.get("section", "")
    values  = params.get("values", {})
    if section:
        config.update_section(section, values)
    else:
        for key, value in params.get("settings", {}).items():
            config.set(key, value)
    if section == "llm" or any(k.startswith("llm") for k in params.get("settings", {}).keys()):
        try:
            for ch in _channel_manager.channels():
                if ch.runtime is not None:
                    ch.runtime.orchestrator.refresh_llm()
        except Exception as e:
            log_error(f"LLM refresh failed: {e}")
    return {"config": config.get_all()}


# ---- Intents ----

def _intents_get(params):
    return {"intents": intent_store.get_registered_intents()}


def _intents_update(params):
    intent_type = params.get("intent_type", "")
    if not intent_type:
        raise ValueError("Missing 'intent_type' in params")
    success = intent_store.update_intent(intent_type, params)
    return {"success": success}


def _intents_delete(params):
    intent_type = params.get("intent_type", "")
    if not intent_type:
        raise ValueError("Missing 'intent_type' in params")
    success = intent_store.delete_intent(intent_type)
    return {"success": success}


def _history_get(params):
    limit = int(params.get("limit", 100)) if params else 100
    return {"history": history_store.get_history(limit)}


def _history_clear_session(params):
    old_id = history_store.archive_current_session()
    return {"success": True, "archived_session_id": old_id, "new_session_id": history_store.get_current_session_id()}


# ---- LLM ----

def _llm_test(params):
    from modules.host.ai_engine_backend import AIEngineBackend as LLMClient
    client = LLMClient()
    result = client.simple_chat(
        "Reply exactly with: OK. I am working correctly.",
        system_prompt="You are a test assistant. Reply in plain text.",
    )
    return {"response": result[:200], "provider": client._provider}


def _llm_providers(params):
    import subprocess
    ai_engine_path = project_path("ai_engine", "ai_engine.py")
    result = subprocess.run(
        [sys.executable, ai_engine_path, "--get-provider"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return {"providers": json.loads(result.stdout)}


def _llm_logs(params):
    config   = ConfigManager()
    log_file = config.get("llm.log_file", "llm_engine.log")
    lines    = int(params.get("lines", 200))
    if not os.path.isabs(log_file):
        log_file = project_path(log_file)
    if not os.path.exists(log_file):
        return {"logs": [], "total_lines": 0}
    with open(log_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return {"logs": all_lines[-lines:], "total_lines": len(all_lines)}


# ---- MCP ----

def _mcp_status_snapshot():
    """Build {server: {connected, tools_count}} for all configured MCP servers."""
    config = ConfigManager()
    mcp_servers = config.get("mcp_servers", {})
    status_info = {}
    for name in mcp_servers:
        client = mcp_registry.get_client(name)
        status_info[name] = {
            "connected": client.is_connected() if client else False,
            "tools_count": len(client.get_tools()) if client and client.is_connected() else 0,
        }
    return status_info


def _mcp_servers_get(params):
    config = ConfigManager()
    mcp_servers = config.get("mcp_servers", {})
    mcp_registry.initialize()
    return {"servers": mcp_servers, "status": _mcp_status_snapshot()}


def _mcp_servers_save(params):
    name = params.get("name", "")
    if not name:
        raise ValueError("Missing 'name' in params")
    config = ConfigManager()
    mcp_servers = config.get("mcp_servers", {})
    server_config = {k: v for k, v in params.items() if k != "name"}
    mcp_servers[name] = server_config
    config.update_section("mcp_servers", mcp_servers)
    try:
        mcp_registry.reload()
        _sync_mcp_tools()
    except Exception as e:
        log_error(f"MCP registry reload failed: {e}")
    return {"success": True}


def _mcp_servers_delete(params):
    name = params.get("name", "")
    if not name:
        raise ValueError("Missing 'name' in params")
    config = ConfigManager()
    mcp_servers = config.get("mcp_servers", {})
    if name in mcp_servers:
        del mcp_servers[name]
        config.update_section("mcp_servers", mcp_servers)
    try:
        mcp_registry.reload()
        _sync_mcp_tools()
    except Exception as e:
        log_error(f"MCP registry reload failed: {e}")
    return {"success": True}


def _mcp_test(params):
    name = params.get("name", "test-server")
    server_config = params.get("config", {})
    if not server_config:
        raise ValueError("No server config provided")
    client = create_mcp_client(name, server_config)
    result = client.test_connection()
    return {"connected": result.get("connected", False), "result": result}


def _mcp_tools(params):
    intent = params.get("intent", "")
    if intent:
        tools = mcp_registry.get_tools_for_intent(intent)
        return {
            "intent": intent,
            "tools": [t.to_tool_definition() for t in tools],
            "tools_count": len(tools),
        }
    return {"servers": mcp_registry.get_all_tools()}


def _mcp_reload(params):
    mcp_registry.reload()
    _sync_mcp_tools()
    return {"success": True}


# ---- Plugins ----

def _plugins_get(params):
    tools  = global_tool_registry.get_all_as_list()
    intents = global_tool_registry.get_intents()
    return {"tools": tools, "intents": sorted(intents), "total": len(tools)}


def _plugins_toggle(params):
    tool_name = params.get("tool_name", "")
    if not tool_name:
        raise ValueError("Missing 'tool_name' in params")
    tool = global_tool_registry.get(tool_name)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")
    enabled = params.get("enabled")
    if enabled is None:
        enabled = not tool.enabled
    success = global_tool_registry.set_enabled(tool_name, enabled)
    if not success:
        raise ValueError(f"Tool '{tool_name}' not found")
    save_tool_config(global_tool_registry)
    return {"name": tool_name, "enabled": enabled}


def _plugins_intents(params):
    tool_name = params.get("tool_name", "")
    if not tool_name:
        raise ValueError("Missing 'tool_name' in params")
    tool = global_tool_registry.get(tool_name)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")
    tool.intents = list(params.get("intents", []))
    save_tool_config(global_tool_registry)
    return {"name": tool_name, "intents": tool.intents}


def _plugins_detail(params):
    tool_name = params.get("tool_name", "")
    if not tool_name:
        raise ValueError("Missing 'tool_name' in params")
    tool = global_tool_registry.get(tool_name)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")
    return {"tool": tool.to_dict()}


# ---- Cron（定时任务）----

def _cron_list(params):
    scheduler = get_scheduler()
    next_runs = {}
    for task in cron_store.load_tasks():
        nxt = scheduler.get_next_run(task["id"])
        if nxt:
            next_runs[task["id"]] = nxt
    return {
        "success": True,
        "tasks": cron_store.load_tasks(),
        "next_runs": next_runs,
        "scheduler": scheduler.get_status(),
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "output_channels": get_dispatcher().available(),
    }


def _cron_create(params):
    fields = params or {}
    for required in ("title", "time", "repeat", "task_type", "description"):
        if not str(fields.get(required, "")).strip():
            raise ValueError(f"Missing '{required}' in params")
    task = cron_store.create_task(fields)
    _reload_scheduler_if_started()
    return {"success": True, "task": task}


def _cron_update(params):
    data = dict(params or {})
    task_id = data.get("id", "")
    if not task_id:
        raise ValueError("Missing 'id' in params")
    try:
        task = cron_store.update_task(task_id, data)
    except cron_store.CronValidationError as e:
        raise ValueError(str(e))
    except KeyError:
        raise ValueError(f"Cron task '{task_id}' not found")
    _reload_scheduler_if_started()
    return {"success": True, "task": task}


def _cron_delete(params):
    task_id = (params or {}).get("id", "")
    if not task_id:
        raise ValueError("Missing 'id' in params")
    deleted = cron_store.delete_task(task_id)
    if not deleted:
        raise ValueError(f"Cron task '{task_id}' not found")
    _reload_scheduler_if_started()
    return {"success": True, "id": task_id}


def _cron_results(params):
    data = params or {}
    results = cron_store.get_results(
        limit=data.get("limit", 100),
        cron_id=data.get("cron_id"),
    )
    return {"success": True, "results": results, "total": len(results)}


def _cron_status(params):
    return {
        "success": True,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **get_scheduler().get_status(),
    }


def _cron_start(params):
    get_scheduler().start()
    return {"success": True, **get_scheduler().get_status()}


def _cron_stop(params):
    get_scheduler().stop()
    return {"success": True, **get_scheduler().get_status()}


def _reload_scheduler_if_started():
    scheduler = get_scheduler()
    if scheduler.is_started:
        scheduler.reload()


# ============================================================
# Dispatch table: JSON-RPC method → handler function
# ============================================================

METHODS = {
    # Agent
    "agent/router":        _agent_router,
    "agent/status":        _agent_status,
    "agent/cancel":        _agent_cancel,
    # Config
    "config.get":          _config_get,
    "config.update":       _config_update,
    # Intents
    "intents.get":         _intents_get,
    "intents.update":      _intents_update,
    "intents.delete":      _intents_delete,
    # Logs / History
    "history.get":         _history_get,
    "history.clear_session": _history_clear_session,
    # LLM
    "llm.test":            _llm_test,
    "llm.providers":       _llm_providers,
    "llm.logs":            _llm_logs,
    # MCP
    "mcp.servers":         _mcp_servers_get,
    "mcp.servers.save":    _mcp_servers_save,
    "mcp.servers.delete":  _mcp_servers_delete,
    "mcp.test":            _mcp_test,
    "mcp.tools":           _mcp_tools,
    "mcp.reload":          _mcp_reload,
    # Plugins
    "plugins.get":         _plugins_get,
    "plugins.toggle":      _plugins_toggle,
    "plugins.intents":     _plugins_intents,
    "plugins.detail":      _plugins_detail,
    # Cron（定时任务）
    "cron.list":           _cron_list,
    "cron.create":         _cron_create,
    "cron.update":         _cron_update,
    "cron.delete":         _cron_delete,
    "cron.results":        _cron_results,
    "cron.status":         _cron_status,
    "cron.start":          _cron_start,
    "cron.stop":           _cron_stop,
}

try:
    from modules.channels.routes import IMBOT_METHODS
    METHODS.update(IMBOT_METHODS)
    log_info(f"[Routes] iBot methods registered: {list(IMBOT_METHODS.keys())}")
except ImportError as e:
    log_error(f"[Routes] Failed to import IMBOT_METHODS: {e}")
except Exception as e:
    log_error(f"[Routes] Failed to register IMBOT_METHODS: {e}")


# ============================================================
# Single JSON-RPC 2.0 entry point
# ============================================================

@api_bp.route("/rpc", methods=["POST"])
def rpc_dispatch():
    """
    POST /api/rpc

    JSON-RPC 2.0 method dispatch.

    Request:
        {"jsonrpc":"2.0","id":"1","method":"<method>","params":{...}}

    Success:
        {"jsonrpc":"2.0","id":"1","result":{...}}

    Error:
        {"jsonrpc":"2.0","id":"1","error":{"code":-32601,"message":"..."}}
    """
    rpc_id = _rpc_id()

    # --- Parse body --------------------------------------------------
    try:
        data = request.get_json(force=True, silent=True)
    except Exception:
        return _rpc_error(PARSE_ERROR, "Parse error", rpc_id)

    if not data or not isinstance(data, dict):
        return _rpc_error(INVALID_REQUEST, "Request body must be a JSON object", rpc_id)

    # --- Validate required fields ------------------------------------
    if data.get("jsonrpc") != "2.0":
        return _rpc_error(INVALID_REQUEST, "Missing or invalid 'jsonrpc' field (must be \"2.0\")", rpc_id)

    method = data.get("method")
    if not method:
        return _rpc_error(INVALID_REQUEST, "Missing 'method' field", rpc_id)

    params = data.get("params", {})
    if not isinstance(params, dict):
        return _rpc_error(INVALID_PARAMS, "'params' must be an object", rpc_id)

    # --- Dispatch ----------------------------------------------------
    log_debug(f"rpc_dispatch method={method}, params_keys={list(params.keys()) if params else []}")
    handler = METHODS.get(method)
    if handler is None:
        available = ", ".join(sorted(METHODS.keys()))
        return _rpc_error(METHOD_NOT_FOUND, f"Method '{method}' not found. Available: {available}", rpc_id)

    try:
        result = handler(params)
        return _rpc_success(result, rpc_id)
    except (ValueError, KeyError) as e:
        return _rpc_error(INVALID_PARAMS, str(e), rpc_id)
    except Exception as e:
        log_error(f"RPC [{method}] error: {e}")
        return _rpc_error(INTERNAL_ERROR, str(e), rpc_id)


# ============================================================
# Web UI Routes (served by the admin app, NOT under /api/rpc)
# ============================================================

def create_admin_routes(app):
    """Create admin web UI routes on the app."""

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/quick-test")
    def quick_test_page():
        return render_template("quick_test.html")

    @app.route("/config")
    def config_page():
        return render_template("config.html")

    @app.route("/history")
    def history_page():
        return render_template("history.html")

    @app.route("/output/<path:filename>")
    def download_file(filename):
        output_dir = project_path("output")
        return send_from_directory(output_dir, filename)

    # Locale serving route (not RPC — serves static JSON)
    @app.route("/api/admin/locale/<lang>")
    def serve_locale(lang):
        locale_dir = project_path("web", "locales")
        safe_lang = lang.replace("..", "").replace("/", "").replace("\\", "")
        filepath = os.path.join(locale_dir, f"{safe_lang}.json")
        if not os.path.exists(filepath):
            return _rpc_error(METHOD_NOT_FOUND, "Locale not found")
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _rpc_success(data)

    @app.route("/api/llm-stream")
    def llm_stream():
        request_id = request.args.get("request_id", "")
        if not request_id:
            return jsonify({"error": "Missing request_id"}), 400
        cursor = int(request.args.get("cursor", 0))
        return Response(
            llm_event_stream(request_id, cursor=cursor),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/api/log-stream")
    def log_stream():
        cursor = int(request.args.get("cursor", 0))
        config = ConfigManager()
        log_file = config.get("server.log_file", "debugout.log")
        return Response(
            log_watcher.stream(log_file, PROJECT_ROOT, cursor=cursor),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/api/status-stream")
    def status_stream():
        request_id = request.args.get("request_id", "")
        if not request_id:
            return jsonify({"error": "Missing request_id"}), 400
        cursor = int(request.args.get("cursor", 0))
        return Response(
            status_events.stream(request_id, cursor=cursor),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/api/mcp-status-stream")
    def mcp_status_stream():
        def gen():
            yield "data: " + json.dumps({"type": "snapshot", "status": _mcp_status_snapshot()}) + "\n\n"
            yield from mcp_events.stream(keepalive=30)

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
