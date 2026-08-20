#!/usr/bin/env python3
"""
AI Hybrid-Driven Agent Service
Main entry point for the Flask-based AI Agent service.

Architecture:
- JSON-RPC 2.0 API on configurable RPC port (default: 11555)
- Admin web UI on configurable admin port (default: 11556)
- LangGraph-based dual-loop orchestrator
- Multi-LLM support (Ollama, OpenAI, Gemini, DeepSeek)
- Built-in tools: web_search, image_search, create_ppt, save_code, bash

Usage:
    python3 Helix.py                        # Run with default config
    python3 Helix.py --rpc-port 11555       # Custom RPC port
    python3 Helix.py --admin-port 11556     # Custom admin port
    python3 Helix.py --debug                # Debug mode
"""

import os
import sys
import argparse
import threading
from flask import Flask

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.utils.logger import init_logger, log_info, log_error, log_orchestrator
from modules.config.config_manager import ConfigManager
from modules.app.routes import api_bp, admin_bp, create_admin_routes
from imBots.routes import imbot_bp
from HelixCore.tools.base import tool_registry
from modules.mcp.mcp_registry import registry as mcp_registry


def create_service_app() -> Flask:
    """Create the main service Flask app."""
    app = Flask(__name__)
    # 保持 dict 插入序输出 JSON（默认 sort_keys=True 会打乱 intents 等顺序，
    # 破坏 generic 固定排首位的前端展示保证）
    app.json.sort_keys = False
    app.register_blueprint(api_bp)
    app.register_blueprint(imbot_bp)
    return app


def create_admin_app() -> Flask:
    """Create the admin web UI Flask app."""
    app = Flask(
        __name__,
        template_folder="web/templates",
        static_folder="web/static",
        static_url_path="/static"
    )
    # 与 service app 一致：保持 dict 插入序，避免 jsonify 默认字母排序
    # 破坏 intents 等键序敏感响应的顺序保证
    app.json.sort_keys = False
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(imbot_bp)
    create_admin_routes(app)

    # Add CORS headers
    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    return app


def main():
    parser = argparse.ArgumentParser(description="AI Hybrid Agent Service")
    parser.add_argument("--rpc-port", type=int, default=None, help="JSON-RPC 2.0 API port (default: 11555, endpoint: POST /api/rpc)")
    parser.add_argument("--admin-port", type=int, default=None, help="Admin web UI port (default: 11556)")
    parser.add_argument("--host", type=str, default=None, help="Bind address")
    parser.add_argument("--debug", action="store_true", default=None, help="Debug mode")
    args = parser.parse_args()

    # Initialize config
    config = ConfigManager()

    # Override with CLI args
    rpc_port = args.rpc_port or config.get_rpc_port()
    admin_port = args.admin_port or config.get_admin_port()
    host = args.host or config.get_host()
    debug = args.debug if args.debug is not None else config.is_debug()

    # Initialize logger
    init_logger(config.get("server.log_file", "debugout.log"), console=debug)

    # Suppress Flask/werkzeug access logs (GET / 200, etc.)
    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    log_info(f"Starting AI Hybrid Agent Service...")
    log_info(f"RPC port: {rpc_port}, Admin port: {admin_port}, Host: {host}, Debug: {debug}")

    # ═══ 组合根：显式装配全部依赖并构造 AgentOrchestrator ═══
    from modules.host.intent_store import intent_store
    from modules.host.ai_engine_backend import AIEngineBackend
    from modules.host.event_sink import SSEEventSink
    from HelixCore.orchestrator.orchestrator import AgentOrchestrator
    from modules.host.config_builder import build_agent_config_from_config_manager
    from modules.host.llm_event_bus import LlmEventBusImpl
    from modules.app.routes import configure as configure_routes

    # ① 显式构造外部依赖
    llm_backend = AIEngineBackend()
    event_sink = SSEEventSink()
    intent_provider = intent_store
    agent_config = build_agent_config_from_config_manager()

    # ② Host 驱动工具组装：扫描插件目录 + 读取 Helix.json 配置 + 装载 MCP，形成完整工具集
    from modules.host.plugin_loader import discover_plugins, load_tool_config
    from modules.host.log_sink import HostLogSink
    host_log_sink = HostLogSink()
    tool_registry.set_intent_provider(intent_provider)
    tool_registry.set_logger(host_log_sink)
    discover_plugins(tool_registry)
    load_tool_config(tool_registry)
    log_info(f"Plugin tools registered: {len(tool_registry.get_all())} tool(s)")

    mcp_registry.initialize()
    from plugins.mcp_tools import register_mcp_tools
    register_mcp_tools(tool_registry)
    log_info(f"All tools registered: {len(tool_registry.get_all())} tool(s)")

    # ③ 显式构造编排器：所有依赖显式传参（含已组装好的工具注册表）
    orchestrator = AgentOrchestrator(
        llm_backend=llm_backend,
        config=agent_config,
        event_sink=event_sink,
        intent_provider=intent_provider,
        tool_registry=tool_registry,
        event_bus=LlmEventBusImpl(),
        log=host_log_sink,
        refresh_config=build_agent_config_from_config_manager,
    )

    # ④ 注入 routes（替代模块级全局单例 import）
    configure_routes(orchestrator, tool_registry)

    # ⑤ iBot channel manager setup
    from imBots.manager import ChannelManager
    from imBots.wechat.channel import WeChatChannel
    from imBots.wechat.authenticator import WeChatAuthenticator
    from imBots.wechat.ilink_client import ILinkBotsClient
    from imBots.routes import configure as configure_imbot_routes

    imbot_config = config.get("imbot", {})
    poll_timeout = imbot_config.get("poll_timeout", 50)
    proxy = imbot_config.get("proxy", "") or config.get("server.proxy", "")

    ilink_client = ILinkBotsClient(proxy=proxy or None)
    wechat_auth = WeChatAuthenticator(client=ilink_client)
    wechat_channel = WeChatChannel(
        client=ilink_client,
        authenticator=wechat_auth,
        poll_timeout=poll_timeout,
    )
    channel_manager = ChannelManager()
    channel_manager.register(wechat_channel)
    wechat_channel.restore_session()
    configure_imbot_routes(channel_manager)

    # Create apps
    service_app = create_service_app()
    admin_app = create_admin_app()

    # Add CORS to service app too
    @service_app.after_request
    def add_cors_svc(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    # Run admin app in a separate thread
    def run_admin():
        log_info(f"Admin UI starting on http://{host}:{admin_port}")
        admin_app.run(host=host, port=admin_port, debug=False, use_reloader=False)

    admin_thread = threading.Thread(target=run_admin, daemon=True)
    admin_thread.start()

    # Run service app in main thread
    log_info(f"JSON-RPC API starting on http://{host}:{rpc_port}")
    log_orchestrator("System ready. Waiting for requests...")
    log_orchestrator(f"Admin panel: http://{host}:{admin_port}")
    log_orchestrator(f"RPC endpoint: POST http://{host}:{rpc_port}/api/rpc")

    try:
        service_app.run(host=host, port=rpc_port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        log_info("Shutting down...")
    except Exception as e:
        log_error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
