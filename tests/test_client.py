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


# --- v0.2 tools ----------------------------------------------------------


def test_get_resume_returns_name_and_text():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": "r-1", "name": "Alice Smith", "text": "Senior engineer..."})

    c = _client(handler)
    out = c.get_resume()
    assert out["name"] == "Alice Smith"
    assert out["text"] == "Senior engineer..."
    assert seen["path"] == "/api/v1/resume/profile"


def test_get_offer_fetches_compensation():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"base": 150000, "bonus": 30000, "equity": 1000})

    c = _client(handler)
    out = c.get_offer(5)
    assert out["base"] == 150000
    assert seen["path"] == "/api/v1/applications/5/offer-workspace"


def test_get_application_fetches_full_detail():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": 5, "status": "interviewing", "notes": [{"text": "awaiting feedback"}]})

    c = _client(handler)
    out = c.get_application(5)
    assert out["id"] == 5
    assert out["status"] == "interviewing"
    assert seen["path"] == "/api/v1/applications/5"


def test_save_match_posts_note():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "saved", "application_id": 10})

    c = _client(handler)
    out = c.save_match(42, note="interesting company")
    assert out["application_id"] == 10
    assert seen["path"] == "/api/v1/applications/42/save-watch"
    assert "interesting company" in seen["body"]


def test_dismiss_match_posts_reason():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"dismissed": True})

    c = _client(handler)
    out = c.dismiss_match(42, "wrong_seniority", note="junior only")
    assert out["dismissed"] is True
    assert seen["path"] == "/api/v1/jobs/42/feedback"
    assert "wrong_seniority" in seen["body"]


def test_restore_match_posts_empty_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"restored": True})

    c = _client(handler)
    out = c.restore_match(42)
    assert out["restored"] is True
    assert seen["path"] == "/api/v1/jobs/42/restore"


def test_list_questions_without_application_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"questions": [{"id": 1, "question": "Tell us about yourself"}]})

    c = _client(handler)
    out = c.list_questions()
    assert len(out["questions"]) == 1
    assert "application_id" not in seen["url"]


def test_list_questions_with_application_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"questions": [{"id": 1, "question": "Tell us about yourself", "application_id": 5}]})

    c = _client(handler)
    out = c.list_questions(application_id=5)
    assert len(out["questions"]) == 1
    assert "application_id=5" in seen["url"]


def test_save_question_with_all_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": 7, "saved": True})

    c = _client(handler)
    out = c.save_question("Why us?", answer="Great team", application_id=5, category="culture")
    assert out["id"] == 7
    assert "Why us?" in seen["body"]
    assert "Great team" in seen["body"]
    assert "culture" in seen["body"]


def test_save_question_with_minimal_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": 7, "saved": True})

    c = _client(handler)
    out = c.save_question("Why us?")
    assert out["id"] == 7
    body = seen["body"]
    assert "Why us?" in body
    assert "answer" not in body


def test_track_external_application_with_all_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "created", "application_id": 11})

    c = _client(handler)
    out = c.track_external_application(
        "Acme Corp", "Senior Engineer", url="https://jobs.acme.com/123", location="SF", status="applied", applied_date="2026-07-20", note="via LinkedIn"
    )
    assert out["application_id"] == 11
    assert seen["path"] == "/api/v1/applications"
    assert "Acme Corp" in seen["body"]
    assert "Senior Engineer" in seen["body"]
    assert "applied" in seen["body"]


def test_track_external_application_minimal():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "created", "application_id": 11})

    c = _client(handler)
    out = c.track_external_application("Acme Corp", "Senior Engineer")
    assert out["application_id"] == 11
    body = seen["body"]
    assert "Acme Corp" in body
    assert "Senior Engineer" in body
    assert "url" not in body
