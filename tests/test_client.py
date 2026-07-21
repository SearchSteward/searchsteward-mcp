"""Client tests — REST mapping, HTTPS enforcement, error passthrough, poll.

Uses httpx.MockTransport (built in) so no network and no extra test deps.
"""
import httpx
import pytest

from searchsteward_mcp.client import ApiError, ConfigError, SearchStewardClient


def _client(handler) -> SearchStewardClient:
    c = SearchStewardClient(api_key="ss_pat_test", base_url="https://searchsteward.com")
    c._http = httpx.Client(
        base_url="https://searchsteward.com",
        headers={"Authorization": "Bearer ss_pat_test"},
        transport=httpx.MockTransport(handler),
    )
    return c


# --- config -----------------------------------------------------------------

def test_missing_key_raises():
    with pytest.raises(ConfigError):
        SearchStewardClient(api_key=None, base_url="https://searchsteward.com")


def test_non_https_base_rejected():
    with pytest.raises(ConfigError):
        SearchStewardClient(api_key="ss_pat_x", base_url="http://searchsteward.com")


def test_localhost_http_allowed():
    c = SearchStewardClient(api_key="ss_pat_x", base_url="http://localhost:8505")
    c.close()  # no raise


# --- endpoint mapping -------------------------------------------------------

def test_get_jobs_maps_params_and_drops_none():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Eng", "score_v2": 88}]})

    c = _client(handler)
    data = c.get_jobs({"search": "python", "location": None, "min_compensation_usd": 150000, "page": 2, "page_size": 25})
    assert data["jobs"][0]["id"] == 1
    assert "search=python" in seen["url"]
    assert "min_compensation_usd=150000" in seen["url"]
    assert "location" not in seen["url"]  # None dropped
    assert seen["auth"] == "Bearer ss_pat_test"


def test_apply_track_posts_note():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "success", "application_id": 5})

    c = _client(handler)
    out = c.apply_track(42, note="applied via claude")
    assert out["application_id"] == 5
    assert seen["path"] == "/api/v1/applications/42/apply-track"
    assert "applied via claude" in seen["body"]


def test_error_passthrough_extracts_entitlement_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"detail": {"error": "entitlement_denied", "message": "Radar required", "upgrade_reason": "mcp"}})

    c = _client(handler)
    with pytest.raises(ApiError) as ei:
        c.get_applications({})
    assert ei.value.status_code == 402
    assert "Radar required" in ei.value.detail


def test_error_passthrough_string_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "This endpoint is not available to API keys"})

    c = _client(handler)
    with pytest.raises(ApiError) as ei:
        c.get_job_context(1)
    assert ei.value.status_code == 403
    assert "not available" in ei.value.detail


# --- poll -------------------------------------------------------------------

def test_poll_returns_on_completed():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        status = "running" if calls["n"] < 3 else "completed"
        return httpx.Response(200, json={"status": status, "result": {"summary": "done"}})

    c = _client(handler)
    job = c.poll_llm_job("job-1", timeout=60, interval=0, sleep=lambda _s: None)
    assert job["result"]["summary"] == "done"
    assert calls["n"] == 3


def test_poll_raises_on_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "failed", "error": "quota exceeded"})

    c = _client(handler)
    with pytest.raises(ApiError) as ei:
        c.poll_llm_job("job-2", timeout=60, interval=0, sleep=lambda _s: None)
    assert "quota exceeded" in ei.value.detail
