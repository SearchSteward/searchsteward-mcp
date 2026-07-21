"""Server/tool tests. Skipped when the `mcp` SDK isn't installed."""
import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed")

from searchsteward_mcp import server  # noqa: E402
from searchsteward_mcp.client import ApiError  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.calls = []

    def get_jobs(self, params):
        self.calls.append(("get_jobs", params))
        return {"jobs": [{"id": 1, "title": "Eng", "company": "Acme", "score_v2": 91, "date_discovered": "2026-07-20"}]}

    def get_job_context(self, job_id):
        return {"id": job_id, "title": "Eng", "description": "x" * 5000, "score_v2": 91}

    def get_applications(self, params):
        return {"applications": [{"id": 3, "status": "applied"}]}

    def apply_track(self, job_id, note=None):
        self.calls.append(("apply_track", job_id, note))
        return {"status": "success", "application_id": 9}

    def start_negotiation_playbook(self, application_id):
        return {"job_id": "jb-1"}

    def poll_llm_job(self, job_id):
        return {"status": "completed", "result": {"summary": "negotiate hard"}}


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", fake)
    monkeypatch.setattr(server, "_c", lambda: fake)
    return fake


def _fn(tool_name):
    # FastMCP wraps the callables; the original function stays importable by name.
    return getattr(server, tool_name).fn if hasattr(getattr(server, tool_name), "fn") else getattr(server, tool_name)


def test_search_matches_compacts_rows():
    out = _fn("search_matches")(query="python")
    assert out["count"] == 1
    row = out["matches"][0]
    assert row == {
        "id": 1, "title": "Eng", "company": "Acme", "location": None,
        "salary_low": None, "salary_high": None, "score": 91, "discovered": "2026-07-20",
    }


def test_search_matches_caps_page_size(_fake):
    _fn("search_matches")(query="x")
    _, params = _fake.calls[0]
    assert params["page_size"] == 25


def test_get_job_truncates_description():
    out = _fn("get_job")(job_id=1)
    assert out["description_truncated"] is True
    assert out["description"].endswith("…[truncated]")


def test_log_application_passes_note(_fake):
    out = _fn("log_application")(job_id=42, note="hi")
    assert out["application_id"] == 9
    assert ("apply_track", 42, "hi") in _fake.calls


def test_get_negotiation_playbook_polls_to_result():
    out = _fn("get_negotiation_playbook")(application_id=3)
    assert out["summary"] == "negotiate hard"


def test_tool_error_is_returned_not_raised(monkeypatch, _fake):
    def boom(params):
        raise ApiError(402, "Radar required")

    monkeypatch.setattr(_fake, "get_jobs", boom)
    out = _fn("search_matches")(query="x")
    assert out["error"] is True
    assert out["status"] == 402
