"""Unit tests for the MCP client loopback proxy bypass.

MCP servers bound to the loopback (localhost / 127.0.0.0/8 / ::1) must be
connected directly, never through an HTTP proxy: a proxy resolves the
loopback against its own host — where no MCP server exists — and returns
502 Bad Gateway. See modules/mcp/mcp_client.py:_configure_session.
"""

import requests

from modules.mcp.mcp_client import _configure_session, _is_loopback_url


def test_is_loopback_url_detects_loopback_forms():
    loopback_urls = [
        "http://127.0.0.1:8004/mcp",          # weather MCP default
        "http://localhost:8004/sse",          # SSE endpoint form
        "http://127.0.0.2:8003/mcp",          # any 127/8 address
        "https://localhost.localdomain/",
        "http://[::1]:8004/mcp",              # IPv6 loopback
    ]
    for url in loopback_urls:
        assert _is_loopback_url(url), f"{url} should be loopback"


def test_is_loopback_url_rejects_remote_urls():
    remote_urls = [
        "http://192.168.10.34:8000",          # LAN MCP servers
        "http://192.168.10.39:5555",
        "https://api.open-meteo.com/v1/forecast",
        "http://example.com/mcp",
    ]
    for url in remote_urls:
        assert not _is_loopback_url(url), f"{url} should not be loopback"


def test_is_loopback_url_rejects_empty_or_invalid():
    assert not _is_loopback_url("")
    assert not _is_loopback_url("not a url")


def test_configure_session_bypasses_proxy_for_loopback():
    for url in ("http://127.0.0.1:8004/mcp", "http://localhost:8004/sse"):
        session = _configure_session(requests.Session(), url)
        assert session.trust_env is False, f"loopback {url} must bypass proxy"


def test_configure_session_keeps_proxy_for_remote():
    for url in ("http://192.168.10.34:8000", "https://api.open-meteo.com"):
        session = _configure_session(requests.Session(), url)
        assert session.trust_env is True, f"remote {url} must keep proxy"