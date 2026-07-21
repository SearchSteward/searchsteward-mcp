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

    def get_resume(self):
        return {"id": "r-1", "name": "Alice Smith", "text": "Senior Engineer..."}

    def get_offer(self, application_id):
        return {"base": 150000, "bonus": 30000, "equity": 1000}

    def get_application(self, application_id):
        return {"id": application_id, "status": "interviewing", "notes": [{"text": "awaiting feedback"}]}

    def save_match(self, job_id, note=None):
        self.calls.append(("save_match", job_id, note))
        return {"status": "saved", "application_id": 10}

    def dismiss_match(self, job_id, reason_code, note=None):
        self.calls.append(("dismiss_match", job_id, reason_code, note))
        return {"dismissed": True}

    def restore_match(self, job_id):
        return {"restored": True}

    def list_questions(self, application_id=None):
        return {"questions": [{"id": 1, "question": "Tell us about yourself"}]}

    def save_question(self, question, answer=None, application_id=None, category=None):
        return {"id": 7, "saved": True}

    def track_external_application(self, company, title, url=None, location=None, status=None, applied_date=None, note=None):
        return {"status": "created", "application_id": 11}


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


# --- v0.2 tool tests --------------------------------------------------------


def test_get_resume_returns_name_and_text():
    out = _fn("get_resume")()
    assert out["name"] == "Alice Smith"
    assert out["text"] == "Senior Engineer..."


def test_get_offer_returns_compensation():
    out = _fn("get_offer")(application_id=5)
    assert out["base"] == 150000
    assert out["bonus"] == 30000


def test_get_application_merges_offer_on_success(_fake):
    out = _fn("get_application")(application_id=5)
    assert out["id"] == 5
    assert out["status"] == "interviewing"
    assert out["offer"]["base"] == 150000  # merged offer


def test_get_application_omits_offer_on_not_found(monkeypatch, _fake):
    def boom_offer(application_id):
        raise ApiError(404, "Offer not found")

    monkeypatch.setattr(_fake, "get_offer", boom_offer)
    out = _fn("get_application")(application_id=5)
    assert out["id"] == 5
    assert "offer" not in out  # 404 on offer is silently omitted


def test_save_match_surfaces_application_id(_fake):
    out = _fn("save_match")(job_id=42, note="interesting")
    assert out["application_id"] == 10
    assert ("save_match", 42, "interesting") in _fake.calls


def test_dismiss_match_passes_reason_code(_fake):
    out = _fn("dismiss_match")(job_id=42, reason_code="wrong_seniority", note="junior only")
    assert out["dismissed"] is True
    assert ("dismiss_match", 42, "wrong_seniority", "junior only") in _fake.calls


def test_restore_match_calls_client():
    out = _fn("restore_match")(job_id=42)
    assert out["restored"] is True


def test_list_questions_without_filter():
    out = _fn("list_questions")()
    assert len(out["questions"]) == 1
    assert out["questions"][0]["question"] == "Tell us about yourself"


def test_list_questions_with_filter():
    out = _fn("list_questions")(application_id=5)
    assert len(out["questions"]) == 1


def test_save_question_with_all_fields():
    out = _fn("save_question")(question="Why us?", answer="Great team", application_id=5, category="culture")
    assert out["saved"] is True
    assert out["id"] == 7


def test_track_external_application_surfaces_application_id():
    out = _fn("track_external_application")(
        company="Acme Corp", title="Senior Engineer", url="https://jobs.acme.com/123", location="SF", status="applied", applied_date="2026-07-20", note="via LinkedIn"
    )
    assert out["application_id"] == 11
    assert out["status"] == "created"


def test_track_external_application_minimal():
    out = _fn("track_external_application")(company="Acme Corp", title="Senior Engineer")
    assert out["application_id"] == 11


def test_get_resume_error_returned_not_raised(monkeypatch, _fake):
    def boom():
        raise ApiError(402, "Radar required")

    monkeypatch.setattr(_fake, "get_resume", boom)
    out = _fn("get_resume")()
    assert out["error"] is True
    assert out["status"] == 402


# --- v0.2.1 conversion CTA tests --------------------------------------------


def _capped_feed(monkeypatch, _fake, *, is_free, total_strong, shown, page_echo=None):
    """Make get_jobs return a crafted /jobs response carrying the nudge fields."""
    def fake_get_jobs(params):
        return {
            "jobs": [{"id": 1, "title": "Eng", "company": "Acme", "score_v2": 91}],
            "is_free": is_free,
            "total_strong_matches": total_strong,
            "matches_shown": shown,
        }
    monkeypatch.setattr(_fake, "get_jobs", fake_get_jobs)


def test_upgrade_cta_fires_for_capped_free_user(monkeypatch, _fake):
    _capped_feed(monkeypatch, _fake, is_free=True, total_strong=150, shown=50)
    out = _fn("search_matches")(query="python")
    up = out["upgrade"]
    assert up["reason"] == "feed_depth"
    assert up["feed_cap"] == 50
    assert up["more_behind_paywall"] == 100
    assert "100 more" in up["message"]
    assert "50" in up["message"]  # feed-cap framing, honest about the feed size


def test_upgrade_cta_shows_plus_at_ceiling(monkeypatch, _fake):
    # total_strong is the backend's ≤200 ceiling count → "N+" (at least this many)
    _capped_feed(monkeypatch, _fake, is_free=True, total_strong=200, shown=50)
    out = _fn("search_matches")(query="x")
    assert out["upgrade"]["more_behind_paywall"] == 150
    assert "150+ more" in out["upgrade"]["message"]


def test_no_upgrade_cta_for_paid_user(monkeypatch, _fake):
    _capped_feed(monkeypatch, _fake, is_free=False, total_strong=150, shown=50)
    out = _fn("search_matches")(query="x")
    assert "upgrade" not in out


def test_no_upgrade_cta_when_not_capped(monkeypatch, _fake):
    _capped_feed(monkeypatch, _fake, is_free=True, total_strong=50, shown=50)
    out = _fn("search_matches")(query="x")
    assert "upgrade" not in out


def test_no_upgrade_cta_on_page_2(monkeypatch, _fake):
    # Defense in depth: even if a response leaks a non-zero count on page 2.
    _capped_feed(monkeypatch, _fake, is_free=True, total_strong=150, shown=50)
    out = _fn("search_matches")(query="x", page=2)
    assert "upgrade" not in out


def test_no_upgrade_cta_when_fields_absent():
    # Default _FakeClient.get_jobs returns no nudge fields → no CTA, no crash.
    out = _fn("search_matches")(query="x")
    assert "upgrade" not in out
    assert out["count"] == 1


def test_upgrade_cta_survives_non_numeric_fields(monkeypatch, _fake):
    def fake_get_jobs(params):
        return {"jobs": [], "is_free": True, "total_strong_matches": "oops", "matches_shown": None}
    monkeypatch.setattr(_fake, "get_jobs", fake_get_jobs)
    out = _fn("search_matches")(query="x")  # must not raise
    assert "upgrade" not in out


def test_get_offer_attaches_radar_tip():
    out = _fn("get_offer")(application_id=5)
    assert out["base"] == 150000  # existing behavior intact
    assert "get_negotiation_playbook" in out["radar_tip"]
    assert "Radar plan required" not in out["radar_tip"]  # neutral for paid users


def test_get_offer_error_has_no_radar_tip(monkeypatch, _fake):
    def boom(application_id):
        raise ApiError(403, "forbidden")
    monkeypatch.setattr(_fake, "get_offer", boom)
    out = _fn("get_offer")(application_id=5)
    assert out["error"] is True
    assert "radar_tip" not in out


def test_negotiation_402_relays_upgrade_reason(monkeypatch, _fake):
    # C3 regression guard: the upgrade_reason text must reach the tool result's detail.
    def boom(application_id):
        raise ApiError(402, "You've hit the negotiation cap. Upgrade to Radar to continue.")
    monkeypatch.setattr(_fake, "start_negotiation_playbook", boom)
    out = _fn("get_negotiation_playbook")(application_id=3)
    assert out["error"] is True
    assert out["status"] == 402
    assert "Upgrade to Radar" in out["detail"]
