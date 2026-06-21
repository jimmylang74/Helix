"""
Flask REST API routes for the AI Agent Service.
/api/agent/router - Main agent endpoint
/api/admin/config - Configuration management
/api/admin/mcp/* - MCP management endpoints
/api/admin/... - Other admin endpoints
"""

import json
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, send_from_directory

from modules.core.orchestrator import orchestrator
from modules.config.config_manager import ConfigManager
from modules.agents.intent_router import intent_router
from modules.agents.tool_base import tool_registry
from modules.mcp.mcp_registry import registry as mcp_registry
from modules.mcp.mcp_client import create_mcp_client
from modules.utils.logger import log_info, log_error

# Blueprints
api_bp = Blueprint("api", __name__, url_prefix="/api")
admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


# ============================================================
# API Routes
# ============================================================

@api_bp.route("/agent/router", methods=["POST"])
def agent_router():
    """
    Main agent endpoint.
    Accepts user request and routes to the appropriate agent.

    Request JSON:
    {
        "request": "User's request text",
        "intent": "optional|auto|ppt|research|coding",  # Force a specific intent
        "stream": false  # Whether to stream the response
    }

    Response JSON:
    {
        "success": true,
        "request_id": "req_xxx",
        "intent_type": "research",
        "final_result": "...",
        "generated_files": [...],
        "todos_completed": 3
    }
    """
    try:
        data = request.get_json(force=True)
        if not data or "request" not in data:
            return jsonify({"success": False, "error": "Missing 'request' field"}), 400

        user_request = data["request"]
        forced_intent = data.get("intent", "auto")
        stream = data.get("stream", False)

        # Validate forced intent if provided
        if forced_intent != "auto" and forced_intent not in ("ppt", "research", "coding"):
            return jsonify({
                "success": False,
                "error": f"Invalid intent: {forced_intent}. Must be one of: auto, ppt, research, coding"
            }), 400

        request_id = f"req_{uuid.uuid4().hex[:12]}"
        log_info(f"API request [{request_id}]: intent={forced_intent}, request={user_request[:100]}...")

        # If intent is forced, prepend to request for orchestrator processing
        if forced_intent != "auto":
            user_request = f"[Intent: {forced_intent}] {user_request}"

        # Process via orchestrator
        result = orchestrator.process_request(user_request, request_id)

        return jsonify(result)

    except Exception as e:
        log_error(f"API error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/agent/status/<request_id>", methods=["GET"])
def agent_status(request_id):
    """Get status of a request."""
    state = orchestrator.get_state(request_id)
    if not state:
        return jsonify({"success": False, "error": "Request not found"}), 404
    return jsonify({"success": True, **state})


# ============================================================
# Admin API Routes
# ============================================================

@admin_bp.route("/config", methods=["GET"])
def get_config():
    """Get full configuration."""
    config = ConfigManager()
    return jsonify({"success": True, "config": config.get_all()})


@admin_bp.route("/config", methods=["POST"])
def update_config():
    """Update configuration section."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        config = ConfigManager()
        section = data.get("section", "")
        values = data.get("values", {})

        if section:
            config.update_section(section, values)
        else:
            # Update individual keys
            for key, value in data.get("settings", {}).items():
                config.set(key, value)

        # Refresh LLM if LLM config changed
        if section == "llm" or any(k.startswith("llm") for k in data.get("settings", {}).keys()):
            try:
                orchestrator.refresh_llm()
            except Exception as e:
                log_error(f"LLM refresh failed: {e}")

        return jsonify({"success": True, "config": config.get_all()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/intents", methods=["GET"])
def get_intents():
    """Get all registered intents."""
    return jsonify({
        "success": True,
        "intents": intent_router.get_registered_intents()
    })


@admin_bp.route("/intents/<intent_type>", methods=["POST"])
def update_intent(intent_type):
    """Update or create an intent."""
    try:
        data = request.get_json(force=True)
        success = intent_router.update_intent(intent_type, data)
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/intents/<intent_type>", methods=["DELETE"])
def delete_intent(intent_type):
    """Delete an intent."""
    success = intent_router.delete_intent(intent_type)
    return jsonify({"success": success})


@admin_bp.route("/logs", methods=["GET"])
def get_logs():
    """Get recent log entries."""
    try:
        log_file = request.args.get("file", "debugout.log")
        lines = int(request.args.get("lines", 200))

        import os
        log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), log_file)

        if not os.path.exists(log_path):
            return jsonify({"success": True, "logs": [], "file": log_file})

        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        return jsonify({
            "success": True,
            "logs": all_lines[-lines:],
            "total_lines": len(all_lines),
            "file": log_file
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/history", methods=["GET"])
def get_history():
    """Get request history (from current session)."""
    return jsonify({
        "success": True,
        "history": []
    })


@admin_bp.route("/llm/test", methods=["POST"])
def test_llm():
    """Test LLM connection."""
    try:
        from modules.llm.llm_client import LLMClient
        client = LLMClient()
        result = client.simple_chat(
            "Reply exactly with: OK. I am working correctly.",
            system_prompt="You are a test assistant. Reply in plain text."
        )
        return jsonify({
            "success": True,
            "response": result[:200],
            "provider": client._provider
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# MCP Management API Routes
# ============================================================

@admin_bp.route("/mcp/servers", methods=["GET"])
def get_mcp_servers():
    """Get all MCP server configurations and their status."""
    config = ConfigManager()
    mcp_servers = config.get("mcp_servers", {})
    
    # Ensure registry is initialized for accurate status
    mcp_registry.initialize()
    
    # Get connection status from registry
    status_info = {}
    for name in mcp_servers:
        client = mcp_registry.get_client(name)
        status_info[name] = {
            "connected": client.is_connected() if client else False,
            "tools_count": len(client.get_tools()) if client and client.is_connected() else 0,
        }
    
    return jsonify({
        "success": True,
        "servers": mcp_servers,
        "status": status_info,
    })


@admin_bp.route("/mcp/servers/<name>", methods=["POST"])
def save_mcp_server(name):
    """Create or update an MCP server configuration."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        config = ConfigManager()
        mcp_servers = config.get("mcp_servers", {})
        mcp_servers[name] = data
        config.update_section("mcp_servers", mcp_servers)

        # Reload registry to apply changes
        try:
            mcp_registry.reload()
        except Exception as e:
            log_error(f"MCP registry reload failed: {e}")

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/mcp/servers/<name>", methods=["DELETE"])
def delete_mcp_server(name):
    """Delete an MCP server configuration."""
    try:
        config = ConfigManager()
        mcp_servers = config.get("mcp_servers", {})
        if name in mcp_servers:
            del mcp_servers[name]
            config.update_section("mcp_servers", mcp_servers)

        # Reload registry
        try:
            mcp_registry.reload()
        except Exception as e:
            log_error(f"MCP registry reload failed: {e}")

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/mcp/test", methods=["POST"])
def test_mcp_connection():
    """Test MCP connection with given config."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        name = data.get("name", "test-server")
        server_config = data.get("config", {})

        if not server_config:
            return jsonify({"success": False, "error": "No server config provided"}), 400

        # Create temporary client and test
        client = create_mcp_client(name, server_config)
        result = client.test_connection()

        return jsonify({
            "success": result.get("connected", False),
            "result": result,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/mcp/tools", methods=["GET"])
def get_mcp_tools():
    """Get all MCP tools, optionally filtered by intent."""
    intent = request.args.get("intent", "")
    
    if intent:
        tools = mcp_registry.get_tools_for_intent(intent)
        return jsonify({
            "success": True,
            "intent": intent,
            "tools": [t.to_tool_definition() for t in tools],
            "tools_count": len(tools),
        })
    else:
        all_tools = mcp_registry.get_all_tools()
        return jsonify({
            "success": True,
            "servers": all_tools,
        })


@admin_bp.route("/mcp/reload", methods=["POST"])
def reload_mcp():
    """Reload all MCP connections."""
    try:
        mcp_registry.reload()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Plugin Tool Management API Routes
# ============================================================

@admin_bp.route("/plugins", methods=["GET"])
def get_plugins():
    """Get all registered plugin tools with their metadata."""
    tools = tool_registry.get_all_as_list()
    categories = tool_registry.get_categories()
    return jsonify({
        "success": True,
        "tools": tools,
        "categories": sorted(categories),
        "total": len(tools),
    })


@admin_bp.route("/plugins/<tool_name>/toggle", methods=["POST"])
def toggle_plugin(tool_name):
    """Enable or disable a plugin tool."""
    try:
        data = request.get_json(force=True) if request.data else {}
        enabled = data.get("enabled")
        if enabled is None:
            tool = tool_registry.get(tool_name)
            if tool:
                enabled = not tool.enabled
            else:
                return jsonify({"success": False, "error": f"Tool '{tool_name}' not found"}), 404

        success = tool_registry.set_enabled(tool_name, enabled)
        if success:
            tool_registry.save_enabled_state()
            return jsonify({"success": True, "name": tool_name, "enabled": enabled})
        return jsonify({"success": False, "error": f"Tool '{tool_name}' not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/plugins/<tool_name>", methods=["GET"])
def get_plugin_detail(tool_name):
    """Get detailed info for a specific plugin tool."""
    tool = tool_registry.get(tool_name)
    if not tool:
        return jsonify({"success": False, "error": f"Tool '{tool_name}' not found"}), 404
    return jsonify({"success": True, "tool": tool.to_dict()})


# ============================================================
# Web UI Routes (served by admin port)
# ============================================================

def create_admin_routes(app):
    """Create admin web UI routes on the app."""

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/config")
    def config_page():
        return render_template("config.html")

    @app.route("/logs")
    def logs_page():
        return render_template("logs.html")

    @app.route("/history")
    def history_page():
        return render_template("history.html")

    @app.route("/output/<path:filename>")
    def download_file(filename):
        import os
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")
        return send_from_directory(output_dir, filename)

    # Add locale serving route
    @app.route("/api/admin/locale/<lang>")
    def serve_locale(lang):
        import os, json
        locale_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "web", "locales")
        safe_lang = lang.replace("..", "").replace("/", "").replace("\\", "")
        filepath = os.path.join(locale_dir, f"{safe_lang}.json")
        if not os.path.exists(filepath):
            return jsonify({"error": "Locale not found"}), 404
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return jsonify(data)
