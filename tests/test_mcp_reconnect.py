"""Regression tests for MCP reconnect resilience.

A half-open HTTP session may complete the initialize handshake and then hang
on tools/list. Previously the transport exception escaped into the reconnect
monitor thread, killing it and leaving tools unregistered forever.
"""

import threading
from unittest.mock import patch

import requests

from modules.mcp.mcp_client import MCPClient, TOOL_DISCOVERY_TIMEOUT
from modules.mcp.mcp_registry import MCPRegistry


class _FakeSession:
    def post(self, *args, **kwargs):
        raise requests.exceptions.ReadTimeout("read timed out")

    def close(self):
        pass


def test_request_transport_error_returns_none():
    client = MCPClient("x", {"type": "server", "url": "http://example.test/mcp"})
    client._active_protocol = "streamable_http"
    client._http_session = _FakeSession()
    client._http_endpoint = "http://example.test/mcp"

    assert client._send_request("tools/list") is None
    assert not client._pending, "pending request must be cleaned up on failure"


def test_list_tools_failure_is_reported_not_raised():
    client = MCPClient("x", {"type": "server", "url": "http://example.test/mcp"})
    client._connected = True

    with patch.object(client, "_send_request", return_value=None) as send:
        assert client.list_tools() == []
    assert send.call_args.kwargs["timeout"] == TOOL_DISCOVERY_TIMEOUT
    assert client.tools_discovery_failed
    assert client.get_tools() == []

    with patch.object(client, "_send_request", return_value={"tools": [{"name": "t"}]}):
        assert [t.name for t in client.list_tools()] == ["t"]
    assert not client.tools_discovery_failed


def _fresh_registry():
    """Bypass the singleton __init__ so tests don't touch global state."""
    reg = object.__new__(MCPRegistry)
    reg._reloading = False
    reg._initialized_flag = True
    reg._lifecycle_lock = threading.RLock()
    reg._clients_lock = threading.Lock()
    reg._clients = {}
    reg.config = {"mcp_servers": {}}
    reg.on_server_state_change = None
    return reg


class _FakeClient:
    enabled = True

    def __init__(self, discovery_failed=True):
        self.connected = False
        self.disconnect_count = 0
        self.tools_discovery_failed = discovery_failed
        self.tools = [type("Tool", (), {"name": "t"})()] if not discovery_failed else []

    def is_connected(self):
        return self.connected

    def health_check(self):
        return False

    def connect(self):
        self.connected = True
        return True

    def list_tools(self):
        return self.tools

    def get_tools(self):
        return self.tools

    def disconnect(self):
        self.connected = False
        self.disconnect_count += 1


def test_reconnect_discovery_failure_stays_disconnected():
    reg = _fresh_registry()
    events = []
    reg.on_server_state_change = lambda *args: events.append(args)
    client = _FakeClient(discovery_failed=True)

    with patch.object(reg, "get_all_clients", return_value={"pyWinBots": client}):
        reg._check_and_reconnect()

    assert client.disconnect_count == 1
    assert client.connected is False
    assert events == [], "a failed discovery must never be published as connected"


def test_reconnect_success_notifies_tool_sync():
    reg = _fresh_registry()
    events = []
    reg.on_server_state_change = lambda *args: events.append(args)
    client = _FakeClient(discovery_failed=False)

    with patch.object(reg, "get_all_clients", return_value={"pyWinBots": client}), \
         patch.object(reg, "get_client", return_value=client):
        reg._check_and_reconnect()

    assert client.disconnect_count == 0
    assert events == [("pyWinBots", True, 1)]


def test_test_connection_reports_discovery_failure():
    client = MCPClient("x", {"type": "server", "url": "http://example.test/mcp"})
    with patch.object(client, "connect", return_value=True), \
         patch.object(client, "list_tools", return_value=[]), \
         patch.object(client, "disconnect"):
        client._last_tools_error = "no response from tools/list"
        result = client.test_connection()

    assert result["connected"] is True
    assert result["error"] == "Tool discovery failed"


def test_monitor_loop_survives_a_failed_pass():
    reg = _fresh_registry()
    reg._reconnect_stop = threading.Event()
    calls = []

    def flaky_pass():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("one server blew up")
        reg._reconnect_stop.set()

    with patch.object(reg, "_check_and_reconnect", side_effect=flaky_pass), \
         patch.object(reg, "_get_reconnect_interval", return_value=0):
        reg._reconnect_loop()

    assert len(calls) == 2, "monitor thread must survive a single bad pass"
