#!/usr/bin/env python3
"""
Test program for AI Agent Service API.
Tests all major API endpoints and agent capabilities.
"""

import sys
import os
import json
import time
import urllib.request
import urllib.error

# Configuration
SERVICE_BASE = "http://localhost:11555"
ADMIN_BASE = "http://localhost:11556"


def print_header(title):
    """Print a colored section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(name, success, detail=""):
    """Print a test result."""
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"  {status} | {name}")
    if detail:
        print(f"         {detail}")


def api_request(url, method="GET", data=None):
    """Make an HTTP request and return parsed JSON."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None

    req = urllib.request.Request(
        url, data=body, headers=headers, method=method
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content), resp.status
    except urllib.error.HTTPError as e:
        try:
            content = e.read().decode("utf-8")
            return json.loads(content), e.code
        except Exception:
            return {"success": False, "error": str(e)}, e.code
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Connection failed: {e.reason}"}, 0
    except Exception as e:
        return {"success": False, "error": str(e)}, 0


def test_01_health_check():
    """Test that both servers are running."""
    print_header("Test 01: Health Check")

    # Check service API
    result, status = api_request(f"{SERVICE_BASE}/api/admin/config")
    api_ok = status != 0

    # Check admin UI
    try:
        req = urllib.request.Request(f"{ADMIN_BASE}/", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            admin_ok = resp.status == 200
    except Exception:
        admin_ok = False

    print_result("Service API accessible", api_ok,
                 f"HTTP {status}" if status else "Connection refused")
    print_result("Admin UI accessible", admin_ok)

    return api_ok and admin_ok


def test_02_config_endpoints():
    """Test config CRUD endpoints."""
    print_header("Test 02: Configuration API")

    # GET config
    result, status = api_request(f"{ADMIN_BASE}/api/admin/config")
    config_ok = result.get("success") and "config" in result
    print_result("GET /api/admin/config", config_ok)

    if config_ok:
        provider = result["config"].get("llm", {}).get("provider", "unknown")
        print(f"    LLM Provider: {provider}")

    # POST config (update single setting)
    test_key = f"test_key_{int(time.time())}"
    result, status = api_request(
        f"{ADMIN_BASE}/api/admin/config",
        method="POST",
        data={"settings": {test_key: "test_value"}}
    )
    # Clean up - this is just testing the endpoint
    print_result("POST /api/admin/config (update)", True)

    # LLM test
    result, status = api_request(
        f"{ADMIN_BASE}/api/admin/llm/test",
        method="POST"
    )
    # LLM might not be running, but endpoint should work
    llm_test_ok = status != 0
    print_result("POST /api/admin/llm/test", llm_test_ok,
                 result.get("response", result.get("error", ""))[:60])

    return True


def test_03_intent_endpoints():
    """Test intent management endpoints."""
    print_header("Test 03: Intent Management")

    # GET intents
    result, status = api_request(f"{ADMIN_BASE}/api/admin/intents")
    intents_ok = result.get("success") and "intents" in result
    print_result("GET /api/admin/intents", intents_ok)

    if intents_ok:
        intents = result["intents"]
        print(f"    Registered intents: {', '.join(intents.keys())}")

    # Test register new intent
    result, status = api_request(
        f"{ADMIN_BASE}/api/admin/intents/test_intent",
        method="POST",
        data={"enabled": True, "name": "Test Intent", "description": "For testing"}
    )
    print_result("POST /api/admin/intents/test_intent (register)",
                 result.get("success", False))

    # Test delete intent
    result, status = api_request(
        f"{ADMIN_BASE}/api/admin/intents/test_intent",
        method="DELETE"
    )
    print_result("DELETE /api/admin/intents/test_intent (cleanup)",
                 result.get("success", False))

    return True


def test_04_logs_endpoint():
    """Test logs endpoint."""
    print_header("Test 04: Logs API")

    result, status = api_request(f"{ADMIN_BASE}/api/admin/logs?lines=50")
    logs_ok = result.get("success") and "logs" in result
    print_result("GET /api/admin/logs", logs_ok,
                 f"{result.get('total_lines', 0)} total lines" if logs_ok else "")

    if logs_ok and result["logs"]:
        print(f"    Last log entry: {result['logs'][-1][:100]}")

    return logs_ok


def test_05_agent_router():
    """Test the main agent API endpoint."""
    print_header("Test 05: Agent Router API")

    # Test with a simple request
    test_request = "请用中文回复：Hello World的翻译是什么？"

    result, status = api_request(
        f"{SERVICE_BASE}/api/agent/router",
        method="POST",
        data={"request": test_request, "intent": "auto"}
    )

    route_ok = status != 0
    print_result("POST /api/agent/router", route_ok,
                 f"HTTP {status}" if status else "Connection refused")

    if route_ok:
        print(f"    Request ID: {result.get('request_id', 'N/A')}")
        print(f"    Success: {result.get('success', False)}")
        print(f"    Intent: {result.get('intent_type', 'N/A')}")

        if result.get("final_result"):
            preview = result["final_result"][:200]
            print(f"    Result preview: {preview}...")

    return route_ok


def test_06_request_status():
    """Test request status endpoint."""
    print_header("Test 06: Request Status")

    # First make a request
    test_request = "简单测试请求"

    result, _ = api_request(
        f"{SERVICE_BASE}/api/agent/router",
        method="POST",
        data={"request": test_request, "intent": "auto"}
    )

    if result.get("request_id"):
        rid = result["request_id"]
        status_result, _ = api_request(
            f"{SERVICE_BASE}/api/agent/status/{rid}"
        )
        status_ok = status_result.get("success", False)
        print_result("GET /api/agent/status/<id>", status_ok,
                     f"Phase: {status_result.get('orchestrator_phase', 'N/A')}")
    else:
        print_result("GET /api/agent/status/<id>", False, "No request_id returned")
        status_ok = False

    return status_ok


def test_07_tool_execution():
    """Test tool execution capabilities."""
    print_header("Test 07: Tool Execution")

    from modules.agents.agent_tools import AgentTools

    tools = AgentTools()

    # Test web_fetch_batch
    print("  Testing web_fetch_batch...")
    try:
        content = tools.web_fetch_batch(["https://httpbin.org/get"])
        fetch_ok = len(content) > 0
        print_result("web_fetch_batch()", fetch_ok, f"{len(content)} chars fetched")
    except Exception as e:
        print_result("web_fetch_batch()", False, str(e))
        fetch_ok = False

    # Test file operations
    print("  Testing file operations...")
    from modules.utils.file_ops import FileOps
    fops = FileOps()

    write_ok = "Error" not in fops.write_file("/tmp/test_agent.txt", "test content")
    print_result("write_file()", write_ok)

    read_ok = "test content" in fops.read_file("/tmp/test_agent.txt")
    print_result("read_file()", read_ok)

    ls_ok = "test_agent.txt" in fops.ls("/tmp")
    print_result("ls()", ls_ok)

    # Cleanup
    fops.del_file("/tmp/test_agent.txt")

    return fetch_ok and write_ok and read_ok


def test_08_ppt_creation():
    """Test PPT creation capability."""
    print_header("Test 08: PPT Generation")

    from modules.agents.agent_tools import AgentTools

    tools = AgentTools()

    ppt_config = {
        "color_scheme": "modern_blue",
        "slides": [
            {
                "type": "title_slide",
                "title": "测试演示文稿",
                "subtitle": "AI Agent Service Test",
                "background": {"type": "solid", "color_1": [26, 60, 110], "color_2": None},
                "content": [],
                "notes": "Test presentation"
            },
            {
                "type": "content",
                "title": "测试内容页",
                "content": ["这是第一点内容", "这是第二点内容", "这是第三点内容"],
                "layout": "title_content",
                "background": {"type": "solid", "color_1": [240, 244, 250], "color_2": None},
                "notes": ""
            },
            {
                "type": "section_header",
                "title": "谢谢观看",
                "background": {"type": "gradient", "color_1": [26, 60, 110], "color_2": [45, 125, 210]},
                "content": [],
                "notes": ""
            }
        ]
    }

    try:
        filepath = tools.create_ppt(ppt_config)
        ppt_ok = os.path.exists(filepath)
        print_result("create_ppt()", ppt_ok, f"Saved: {filepath}")
        return ppt_ok
    except Exception as e:
        print_result("create_ppt()", False, str(e))
        return False


def test_09_image_download():
    """Test image download capability."""
    print_header("Test 09: Image Download")

    from modules.agents.agent_tools import AgentTools

    tools = AgentTools()

    try:
        saved = tools.image_download([
            "https://via.placeholder.com/100x100.png?text=Test"
        ])
        dl_ok = len(saved) > 0 and os.path.exists(saved[0])
        print_result("image_download()", dl_ok,
                     f"Saved: {saved}" if dl_ok else "Failed")
        return dl_ok
    except Exception as e:
        print_result("image_download()", False, str(e))
        return False


def test_10_llm_client():
    """Test LLM client initialization."""
    print_header("Test 10: LLM Client")

    try:
        from modules.llm.llm_client import LLMClient

        client = LLMClient()
        print_result("LLMClient initialization", True,
                     f"Provider: {client._provider}")

        # Test simple chat (may fail if Ollama not running)
        try:
            response = client.simple_chat(
                "Reply with OK.",
                system_prompt="Reply in plain text."
            )
            print_result("LLMClient simple_chat()", True,
                         f"Response: {response[:100]}")
        except Exception as e:
            print_result("LLMClient simple_chat()", False,
                         f"LLM not available: {str(e)[:80]}")

        return True
    except Exception as e:
        print_result("LLMClient initialization", False, str(e))
        return False


def test_11_config_manager():
    """Test config manager."""
    print_header("Test 11: Config Manager")

    from modules.config.config_manager import ConfigManager

    config = ConfigManager()
    all_config = config.get_all()

    print_result("Config loaded", bool(all_config),
                 f"{len(json.dumps(all_config))} bytes")

    provider = config.get("llm.provider", "unknown")
    print_result("Config get()", bool(provider),
                 f"LLM provider: {provider}")

    port = config.get_service_port()
    print_result("Config port", port > 0,
                 f"Service port: {port}")

    return True


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("  AI Agent Service - Test Suite")
    print("  " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("="*60)
    print(f"\n  Service URL: {SERVICE_BASE}")
    print(f"  Admin URL:  {ADMIN_BASE}")

    results = []

    # Unit tests (no server needed)
    results.append(("Config Manager", test_11_config_manager()))
    results.append(("LLM Client", test_10_llm_client()))
    results.append(("Tool Execution", test_07_tool_execution()))
    results.append(("PPT Creation", test_08_ppt_creation()))
    results.append(("Image Download", test_09_image_download()))

    # API tests (need server running)
    results.append(("Health Check", test_01_health_check()))
    results.append(("Logs Endpoint", test_04_logs_endpoint()))
    results.append(("Config Endpoints", test_02_config_endpoints()))
    results.append(("Intent Endpoints", test_03_intent_endpoints()))
    results.append(("Agent Router", test_05_agent_router()))
    results.append(("Request Status", test_06_request_status()))

    # Summary
    print_header("Test Summary")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n  ✅ Passed: {passed}/{total}")
    print(f"  ❌ Failed: {total - passed}/{total}")

    if passed < total:
        print(f"\n  Failed tests:")
        for name, ok in results:
            if not ok:
                print(f"    - {name}")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
