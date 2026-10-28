"""Protocol-level checks: what a real MCP client sees over the wire (in-memory transport, real initialize)."""
import importlib.metadata
import json

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from searchsteward_mcp import server
from searchsteward_mcp.client import ApiError


class _Fake:
    def __init__(self, exc=None):
        self.exc = exc

    def get_jobs(self, params):
        if self.exc:
            raise self.exc
        return {"jobs": []}


async def _session_call(tool=None, args=None):
    """Run the real server over memory streams; return (InitializeResult, CallToolResult or None)."""
    srv = server.mcp._mcp_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: srv.run(server_streams[0], server_streams[1], srv.create_initialization_options()))
            try:
                async with ClientSession(client_streams[0], client_streams[1]) as session:
                    init = await session.initialize()
                    res = await session.call_tool(tool, args or {}) if tool else None
                    return init, res
            finally:
                tg.cancel_scope.cancel()


def _run(tool=None, args=None):
    return anyio.run(_session_call, tool, args)


def test_initialize_reports_package_version():
    init, _ = _run()
    assert init.serverInfo.version == importlib.metadata.version("searchsteward-mcp")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error_is_iserror_with_same_message(monkeypatch, status):
    monkeypatch.setattr(server, "_c", lambda: _Fake(ApiError(status, "Invalid or revoked API key")))
    _, res = _run("search_matches", {})
    assert res.isError is True
    body = json.loads(res.content[0].text)
    assert body == {"error": True, "status": status, "detail": "Invalid or revoked API key"}


def test_paywall_402_stays_a_normal_result(monkeypatch):
    # 402/409/404 carry next steps (upgrade URL, use update_preferences): the model should read them as data.
    monkeypatch.setattr(server, "_c", lambda: _Fake(ApiError(402, "Radar required")))
    _, res = _run("search_matches", {})
    assert res.isError is False


def test_success_is_not_error(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Fake())
    _, res = _run("search_matches", {})
    assert res.isError is False
