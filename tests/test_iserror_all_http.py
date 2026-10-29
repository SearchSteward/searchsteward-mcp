"""Every error-shaped tool result is an MCP error (isError true) with the same JSON text; nothing error-shaped is a success payload.

Gemini blind QA 10-29 (MEDIUM): only 401/403 set isError; 404/422/429/402/5xx and the per-leg errors of update_application came back as
normal results, so a client reads them as data. 0.5.3 fixed auth only, and kept 402/404/409 as "normal results because they carry next steps":
the next steps live in the JSON text, which is unchanged here, so the rule is now simply "error-shaped => isError".
"""
import json

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from searchsteward_mcp import server
from searchsteward_mcp.client import ApiError

STATUSES = [401, 403, 404, 422, 429, 500]
EXTRA = [402, 409, 502]


async def _with_session(fn):
    srv = server.mcp._mcp_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: srv.run(server_streams[0], server_streams[1], srv.create_initialization_options()))
            try:
                async with ClientSession(client_streams[0], client_streams[1]) as session:
                    await session.initialize()
                    return await fn(session)
            finally:
                tg.cancel_scope.cancel()


def _call(tool, args):
    async def go(s):
        return await s.call_tool(tool, args)
    return anyio.run(_with_session, go)


def _tools():
    async def go(s):
        return (await s.list_tools()).tools
    return anyio.run(_with_session, go)


def _args(schema):
    """Minimal arguments that satisfy a tool's required inputs."""
    out = {}
    for name in schema.get("required", []):
        p = schema["properties"][name]
        t = p.get("type")
        if "enum" in p:
            out[name] = p["enum"][0]
        elif t == "integer" or t == "number":
            out[name] = 1
        elif t == "boolean":
            out[name] = True
        elif t == "array":
            out[name] = ["x"]
        elif t == "object":
            out[name] = {}
        else:
            out[name] = "x"
    return out


class _Raises:
    """A client whose every method fails with the same ApiError."""
    def __init__(self, status):
        self.status = status

    def __getattr__(self, name):
        def boom(*a, **k):
            raise ApiError(self.status, f"simulated {self.status}")
        return boom


class _Fake:
    def __init__(self, exc):
        self.exc = exc

    def get_jobs(self, params):
        raise self.exc


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


@pytest.mark.parametrize("status", STATUSES + EXTRA)
def test_a_read_tool_http_error_is_iserror_with_the_same_text(monkeypatch, status):
    monkeypatch.setattr(server, "_c", lambda: _Fake(ApiError(status, "boom")))
    res = _call("search_matches", {})
    assert res.isError is True
    assert json.loads(res.content[0].text) == {"error": True, "status": status, "detail": "boom"}


@pytest.mark.parametrize("status", STATUSES + EXTRA)
def test_update_application_note_leg_error_is_iserror(monkeypatch, status):
    monkeypatch.setattr(server, "_c", lambda: _UpdateFake(note_exc=ApiError(status, "x")))
    res = _call("update_application", {"application_id": 3, "note": "hi"})
    assert res.isError is True
    assert json.loads(res.content[0].text)["note_error"]["status"] == status


@pytest.mark.parametrize("status", STATUSES + EXTRA)
def test_update_application_status_leg_error_is_iserror(monkeypatch, status):
    monkeypatch.setattr(server, "_c", lambda: _UpdateFake(status_exc=ApiError(status, "x")))
    res = _call("update_application", {"application_id": 3, "status": "offer"})
    assert res.isError is True
    assert json.loads(res.content[0].text)["status_error"]["status"] == status


def test_update_application_partial_write_is_iserror_but_keeps_what_landed(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _UpdateFake(note_exc=ApiError(500, "note store down")))
    res = _call("update_application", {"application_id": 3, "status": "offer", "note": "hi"})
    body = json.loads(res.content[0].text)
    assert res.isError is True and body["partial"] is True and body["updated"] == {"status": "success"}


@pytest.mark.parametrize("status", [404, 422, 429, 500])
def test_every_tool_that_calls_the_api_reports_http_errors_as_iserror(monkeypatch, status):
    monkeypatch.setattr(server, "_c", lambda: _Raises(status))
    tools = _tools()
    assert len(tools) >= 20
    bad = []
    for t in tools:
        res = _call(t.name, _args(t.inputSchema))
        text = res.content[0].text if res.content else ""
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        error_shaped = isinstance(body, dict) and (bool(body.get("error")) or any(isinstance(v, dict) and v.get("error") for v in body.values()))
        if error_shaped and res.isError is not True:
            bad.append(t.name)
    assert bad == [], f"error-shaped result with isError false: {bad}"


@pytest.mark.parametrize("args,code", [({"titles": []}, "needs_anchor")])
def test_string_shaped_errors_are_iserror(monkeypatch, args, code):
    monkeypatch.setattr(server, "_c", lambda: _Raises(500))
    res = _call("set_up_search", args)
    body = json.loads(res.content[0].text)
    assert body.get("error") == code and res.isError is True


def test_a_successful_tool_result_is_not_an_error(monkeypatch):
    class _Ok:
        def get_jobs(self, params):
            return {"jobs": []}
    monkeypatch.setattr(server, "_c", lambda: _Ok())
    assert _call("search_matches", {}).isError is False
