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


def test_paywall_402_is_iserror_and_keeps_its_text(monkeypatch):
    # 0.5.4: reversed from "402 stays a normal result". The next steps (upgrade URL) live in the JSON text, which is unchanged.
    monkeypatch.setattr(server, "_c", lambda: _Fake(ApiError(402, "Radar required")))
    _, res = _run("search_matches", {})
    assert res.isError is True
    assert json.loads(res.content[0].text) == {"error": True, "status": 402, "detail": "Radar required"}


def test_success_is_not_error(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Fake())
    _, res = _run("search_matches", {})
    assert res.isError is False


class _UpdateFake:
    def __init__(self, status_exc=None, note_exc=None):
        self.status_exc, self.note_exc = status_exc, note_exc

    def patch_application(self, aid, body):
        if self.status_exc:
            raise self.status_exc
        return {"status": "success"}

    def add_note(self, aid, note):
        if self.note_exc:
            raise self.note_exc
        return {"id": 1}


@pytest.mark.parametrize("status", [401, 403])
def test_update_application_auth_error_in_a_leg_is_iserror(monkeypatch, status):
    # QA item 9: the 403/401 sat inside {"note_error": {...}} and came back with isError false.
    monkeypatch.setattr(server, "_c", lambda: _UpdateFake(note_exc=ApiError(status, "read-only key")))
    _, res = _run("update_application", {"application_id": 3, "note": "hi"})
    assert res.isError is True
    assert json.loads(res.content[0].text)["note_error"]["status"] == status


def test_update_application_status_leg_auth_error_is_iserror(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _UpdateFake(status_exc=ApiError(403, "read-only key")))
    _, res = _run("update_application", {"application_id": 3, "status": "offer"})
    assert res.isError is True


def test_update_application_non_auth_leg_error_is_iserror_and_keeps_the_landed_write(monkeypatch):
    # 0.5.4: reversed from "stays a result". The text still carries updated + partial, so the model sees what landed.
    monkeypatch.setattr(server, "_c", lambda: _UpdateFake(note_exc=ApiError(500, "note store down")))
    _, res = _run("update_application", {"application_id": 3, "status": "offer", "note": "hi"})
    assert res.isError is True
    body = json.loads(res.content[0].text)
    assert body["partial"] is True and body["updated"] == {"status": "success"} and body["note_error"]["status"] == 500
