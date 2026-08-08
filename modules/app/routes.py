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
from modules.utils import log_watcher, history_store

from modules.host.tool_context import ToolContext
from modules.host.plugin_loader import save_tool_config
from modules.config.config_manager import ConfigManager
from modules.host.intent_store import intent_store
from modules.mcp.mcp_registry import registry as mcp_registry
from modules.mcp.mcp_client import create_mcp_client
from modules.utils.logger import log_info, log_error, log_debug


# 运行时注入：编排器与工具注册表由组合根（Helix.py）显式构造后经 configure() 注入
_orchestrator: Any = None
_tool_registry: Any = None


def configure(orchestrator, tool_registry):
    """组合根注入编排器与工具注册表实例（替代模块级全局单例 import）。"""
    global _orchestrator, _tool_registry
    _orchestrator = orchestrator
    _tool_registry = tool_registry


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
        if not ToolContext(request_id).answer(answer):
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
        registered = intent_store.get_registered_intents()
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
            result = _orchestrator.process_request(user_request, request_id, forced_intent=forced_intent)
        finally:
            # Host 侧收尾：请求结束（成功/失败/取消/异常）即唤醒可能仍阻塞的 ask_user
            ToolContext(request_id).cancel()
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
    state = _orchestrator.get_state(request_id)
    if not state:
        raise ValueError(f"Request '{request_id}' not found")
    return state


def _agent_cancel(params):
    request_id = params.get("request_id", "")
    if not request_id:
        raise ValueError("Missing 'request_id' in params")

    cancelled = _orchestrator.cancel_request(request_id)

    if not cancelled:
        raise ValueError(f"Request '{request_id}' not found or already completed")

    # 先由编排器置 cancelled 标志，再唤醒等待中的 ask_user（Host 侧负责）
    ToolContext(request_id).cancel()

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
            _orchestrator.refresh_llm()
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
    ai_engine_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "ai_engine", "ai_engine.py",
    )
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
        log_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            log_file,
        )
    if not os.path.exists(log_file):
        return {"logs": [], "total_lines": 0}
    with open(log_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return {"logs": all_lines[-lines:], "total_lines": len(all_lines)}


# ---- MCP ----

def _mcp_servers_get(params):
    config = ConfigManager()
    mcp_servers = config.get("mcp_servers", {})
    mcp_registry.initialize()
    status_info = {}
    for name in mcp_servers:
        client = mcp_registry.get_client(name)
        status_info[name] = {
            "connected": client.is_connected() if client else False,
            "tools_count": len(client.get_tools()) if client and client.is_connected() else 0,
        }
    return {"servers": mcp_servers, "status": status_info}


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
        from plugins.mcp_tools import register_mcp_tools
        register_mcp_tools(_tool_registry)
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
        from plugins.mcp_tools import register_mcp_tools
        register_mcp_tools(_tool_registry)
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
    from plugins.mcp_tools import register_mcp_tools
    register_mcp_tools(_tool_registry)
    return {"success": True}


# ---- Plugins ----

def _plugins_get(params):
    tools  = _tool_registry.get_all_as_list()
    intents = _tool_registry.get_intents()
    return {"tools": tools, "intents": sorted(intents), "total": len(tools)}


def _plugins_toggle(params):
    tool_name = params.get("tool_name", "")
    if not tool_name:
        raise ValueError("Missing 'tool_name' in params")
    tool = _tool_registry.get(tool_name)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")
    enabled = params.get("enabled")
    if enabled is None:
        enabled = not tool.enabled
    success = _tool_registry.set_enabled(tool_name, enabled)
    if not success:
        raise ValueError(f"Tool '{tool_name}' not found")
    save_tool_config(_tool_registry)
    return {"name": tool_name, "enabled": enabled}


def _plugins_intents(params):
    tool_name = params.get("tool_name", "")
    if not tool_name:
        raise ValueError("Missing 'tool_name' in params")
    tool = _tool_registry.get(tool_name)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")
    tool.intents = list(params.get("intents", []))
    save_tool_config(_tool_registry)
    return {"name": tool_name, "intents": tool.intents}


def _plugins_detail(params):
    tool_name = params.get("tool_name", "")
    if not tool_name:
        raise ValueError("Missing 'tool_name' in params")
    tool = _tool_registry.get(tool_name)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")
    return {"tool": tool.to_dict()}


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
}


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
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "output",
        )
        return send_from_directory(output_dir, filename)

    # Locale serving route (not RPC — serves static JSON)
    @app.route("/api/admin/locale/<lang>")
    def serve_locale(lang):
        locale_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "web", "locales",
        )
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
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cursor = int(request.args.get("cursor", 0))
        config = ConfigManager()
        log_file = config.get("server.log_file", "debugout.log")
        return Response(
            log_watcher.stream(log_file, project_root, cursor=cursor),
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
