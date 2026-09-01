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

import atexit
import os
import signal
import sys
import argparse
import threading
from flask import Flask

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.utils.logger import init_logger, log_info, log_error, log_orchestrator
from modules.config.config_manager import ConfigManager
from modules.app.routes import api_bp, admin_bp, create_admin_routes
from modules.channels.routes import imbot_bp
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

    # ═══ 组合根：装配共享工具池，再为每个通道构建私有运行时 ═══
    from modules.app.routes import configure as configure_routes
    from modules.channels.manager import ChannelManager
    from modules.channels.routes import configure as configure_channel_routes
    from modules.channels.runtime import build_channel_runtime
    from modules.channels.web.channel import WebChannel
    from imChannels.wechat.authenticator import WeChatAuthenticator
    from imChannels.wechat.channel import WeChatChannel
    from imChannels.wechat.ilink_client import ILinkBotsClient

    # ① Host 驱动共享工具池：扫描插件目录 + 读取 Helix.json 配置 + 装载 MCP。
    #    该池仅作为通用工具来源；各通道私有 registry 在装配时从此复制，
    #    并追加本通道专属的 ask_user / get_context / clear_context 实例。
    from modules.host.plugin_loader import discover_plugins, load_tool_config
    discover_plugins(tool_registry)
    load_tool_config(tool_registry)
    log_info(f"Plugin tools registered: {len(tool_registry.get_all())} tool(s)")

    mcp_registry.initialize()
    from plugins.mcp_tools import register_mcp_tools
    register_mcp_tools(tool_registry)
    log_info(f"All tools registered: {len(tool_registry.get_all())} tool(s)")

    mcp_registry.initialize()
    from plugins.mcp_tools import register_mcp_tools
    register_mcp_tools(tool_registry)
    log_info(f"All tools registered: {len(tool_registry.get_all())} tool(s)")

    # ═══ 进程退出时回收 MCP 子进程 ═══
    # Helix 退出（正常返回 / 异常 / SIGINT / SIGTERM）时终止 spawn 的子进程
    # （内置 HTTP 服务器如 weather_mcp）并断开全部 MCP 客户端（含 stdio 子进程），
    # 避免子进程残留为孤儿继续占用端口。SIGKILL 场景由子进程侧 PR_SET_PDEATHSIG 兜底。
    def _shutdown_mcp_registry() -> None:
        try:
            mcp_registry.shutdown()
        except Exception as e:
            log_error(f"MCP Registry shutdown on exit failed: {e}")

    atexit.register(_shutdown_mcp_registry)

    def _handle_exit_signal(signum: int, frame) -> None:
        log_info(f"Received signal {signum}, shutting down MCP servers...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_exit_signal)
    signal.signal(signal.SIGINT, _handle_exit_signal)

    # ② 构造并注册通道（Web 快速测试 + 微信）
    web_channel = WebChannel()

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
    channel_manager.register(web_channel)
    channel_manager.register(wechat_channel)

    # ②'' 输出通道注册表（通用跨通道推送）：wechat 通道即 iLinkBot 输出，
    #     任意通道可在此 register 为 sink，供 cron 等消费者跨通道推送
    from modules.channels.dispatcher import get_dispatcher
    get_dispatcher().register("ilinkbot", wechat_channel, label="iLinkBot")

    # ②' 定时任务通道：Helix 启动即拉起调度器线程（区别于系统 crond，
    #    由 Helix 后端自维护）。不注册 ask_user 三件套 —— 定时任务为
    #    一次性自动执行，无提问、无上下文关联
    from modules.channels.cron.channel import CronChannel

    cron_channel = CronChannel()
    channel_manager.register(cron_channel)

    # ③ 每通道装配私有运行时（独立 LLM 后端/日志、事件出口、broker、
    #    工具表与编排器）；须在共享工具池就绪后执行，私有 registry
    #    才能复制到完整工具集
    for ch in (web_channel, wechat_channel):
        build_channel_runtime(ch)
    build_channel_runtime(cron_channel, include_channel_tools=False)

    wechat_channel.restore_session()

    # 启动定时任务调度（start 幂等：已 started 直接返回；先幂等迁移
    # cron.json 补齐 output_channels 字段，原字段值原样保留）
    from modules.channels.cron import store as cron_store
    cron_store.ensure_schema()
    cron_channel.start()

    # ④ 注入 routes：RPC agent/* 与用户应答/取消经 ChannelManager 落到对应通道，
    #    imbot/* 管理接口同样经 ChannelManager 分发
    configure_routes(channel_manager)
    configure_channel_routes(channel_manager)

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
