#!/usr/bin/env python3
"""
AI Hybrid-Driven Agent Service
Main entry point for the Flask-based AI Agent service.

Architecture:
- Flask REST API on configurable service port (default: 11555)
- Admin web UI on configurable admin port (default: 11556)
- LangGraph-based dual-loop orchestrator
- Multi-LLM support (Ollama, OpenAI, Gemini, DeepSeek)
- Built-in tools: web_search, image_search, PPT generation, coding

Usage:
    python3 server.py                    # Run with default config
    python3 server.py --port 11555       # Custom service port
    python3 server.py --admin-port 11556 # Custom admin port
    python3 server.py --debug            # Debug mode
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
from modules.agents.tool_base import tool_registry


def create_service_app() -> Flask:
    """Create the main service Flask app."""
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app


def create_admin_app() -> Flask:
    """Create the admin web UI Flask app."""
    app = Flask(
        __name__,
        template_folder="web/templates",
        static_folder="web/static",
        static_url_path="/static"
    )
    app.register_blueprint(admin_bp)
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
    parser.add_argument("--port", type=int, default=None, help="Service port")
    parser.add_argument("--admin-port", type=int, default=None, help="Admin port")
    parser.add_argument("--host", type=str, default=None, help="Bind address")
    parser.add_argument("--debug", action="store_true", default=None, help="Debug mode")
    args = parser.parse_args()

    # Initialize config
    config = ConfigManager()

    # Override with CLI args
    service_port = args.port or config.get_service_port()
    admin_port = args.admin_port or config.get_admin_port()
    host = args.host or config.get_host()
    debug = args.debug if args.debug is not None else config.is_debug()

    # Initialize logger
    init_logger("debugout.log", console=True)
    log_info(f"Starting AI Hybrid Agent Service...")
    log_info(f"Service port: {service_port}, Admin port: {admin_port}, Host: {host}, Debug: {debug}")

    # Initialize plugin tool registry (auto-scans plugins/ directory)
    tool_registry.initialize()
    log_info(f"Plugin tools registered: {len(tool_registry.get_all())} tool(s)")

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
        admin_app.run(host=host, port=admin_port, debug=debug, use_reloader=False)

    admin_thread = threading.Thread(target=run_admin, daemon=True)
    admin_thread.start()

    # Run service app in main thread
    log_info(f"Service API starting on http://{host}:{service_port}")
    log_orchestrator("System ready. Waiting for requests...")
    log_orchestrator(f"Admin panel: http://{host}:{admin_port}")
    log_orchestrator(f"API endpoint: POST http://{host}:{service_port}/api/agent/router")

    try:
        service_app.run(host=host, port=service_port, debug=debug, use_reloader=False)
    except KeyboardInterrupt:
        log_info("Shutting down...")
    except Exception as e:
        log_error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
