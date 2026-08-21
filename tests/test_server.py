"""Server/tool tests. Skipped when the `mcp` SDK isn't installed."""
from datetime import datetime, timedelta, timezone

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

    def get_review_candidates(self, params):
        self.calls.append(("get_review_candidates", params))
        return {"candidates": [{"id": 5, "title": "Eng", "evaluated": False}], "count": 1}

    def submit_match_verdict(self, job_id, verdict, note=None):
        self.calls.append(("submit_match_verdict", job_id, verdict, note))
        return {"stored": True, "job_id": job_id, "verdict": verdict}

    def get_review_summary(self):
        return {"counts": {"should_surface": 2}, "total": 2}


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


def test_update_application_requires_status_or_note():
    out = _fn("update_application")(application_id=3)
    assert out["error"] is True


def test_update_application_both_succeed(monkeypatch, _fake):
    monkeypatch.setattr(_fake, "patch_application", lambda aid, body: {"ok": True}, raising=False)
    monkeypatch.setattr(_fake, "add_note", lambda aid, note: {"note_id": 1}, raising=False)
    out = _fn("update_application")(application_id=3, status="offer", note="hi")
    assert out["updated"] == {"ok": True}
    assert out["note"] == {"note_id": 1}
    assert "partial" not in out


def test_update_application_reports_partial_when_note_fails(monkeypatch, _fake):
    # Status write commits, note write fails: the caller must SEE that the status
    # landed (partial=True) rather than a clean top-level error it would retry on.
    monkeypatch.setattr(_fake, "patch_application", lambda aid, body: {"ok": True}, raising=False)

    def boom(aid, note):
        raise ApiError(500, "note store down")

    monkeypatch.setattr(_fake, "add_note", boom, raising=False)
    out = _fn("update_application")(application_id=3, status="offer", note="hi")
    assert out["updated"] == {"ok": True}
    assert out["note_error"]["status"] == 500
    assert out["partial"] is True


def test_list_questions_wraps_bare_list(monkeypatch, _fake):
    # The real /questions endpoint returns a JSON array; the tool must hand back a
    # dict or FastMCP's return validation rejects it (the live-smoke failure).
    monkeypatch.setattr(_fake, "list_questions", lambda application_id=None: [{"id": 1}])
    out = _fn("list_questions")()
    assert out == {"questions": [{"id": 1}]}


def test_list_applications_passes_filters(_fake):
    out = _fn("list_applications")(status="applied", page=2)
    assert out["applications"][0]["id"] == 3


def test_review_candidates_passes_params(_fake):
    out = _fn("review_candidates")(query="staff", limit=10, include_labelled=True)
    assert out["count"] == 1
    _, params = _fake.calls[0]
    assert params == {"query": "staff", "limit": 10, "include_labelled": True}


def test_submit_match_verdict_passes_through(_fake):
    out = _fn("submit_match_verdict")(job_id=5, verdict="should_surface", note="great role")
    assert out["stored"] is True
    assert ("submit_match_verdict", 5, "should_surface", "great role") in _fake.calls


def test_review_summary_returns_counts(_fake):
    out = _fn("review_summary")()
    assert out["total"] == 2
    assert out["counts"]["should_surface"] == 2


def test_review_tool_error_is_returned_not_raised(monkeypatch, _fake):
    def boom():
        raise ApiError(403, "not available to API keys")

    monkeypatch.setattr(_fake, "get_review_summary", boom)
    out = _fn("review_summary")()
    assert out["error"] is True and out["status"] == 403


def test_feed_depth_cta_leads_with_monitoring(_fake):
    # The upgrade nudge must sell ongoing monitoring/alerts, not just raw feed depth
    # (a good ranker makes "more rows" a weak, trivially-bypassed pitch).
    data = {"is_free": True, "total_strong_matches": 40, "matches_shown": 25, "strong_90_count": 3}
    from searchsteward_mcp import server as _srv
    cta = _srv._feed_depth_upgrade(data, page=1)
    assert cta is not None
    assert "match appears" in cta["message"]  # the monitoring hook
    assert cta["more_behind_paywall"] == 15


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


def _capped_feed(monkeypatch, _fake, *, is_free, total_strong, shown, strong_90=0):
    """Make get_jobs return a crafted /jobs response carrying the nudge fields."""
    def fake_get_jobs(params):
        return {
            "jobs": [{"id": 1, "title": "Eng", "company": "Acme", "score_v2": 91}],
            "is_free": is_free,
            "total_strong_matches": total_strong,
            "matches_shown": shown,
            "strong_90_count": strong_90,
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


def test_upgrade_cta_includes_high_fit_tier(monkeypatch, _fake):
    _capped_feed(monkeypatch, _fake, is_free=True, total_strong=150, shown=50, strong_90=12)
    out = _fn("search_matches")(query="x")
    msg = out["upgrade"]["message"]
    assert "12" in msg and "90%+" in msg
    assert "locked" in msg  # loss framing
    assert out["upgrade"]["strong_90_count"] == 12


def test_upgrade_cta_omits_tier_when_zero(monkeypatch, _fake):
    _capped_feed(monkeypatch, _fake, is_free=True, total_strong=150, shown=50, strong_90=0)
    out = _fn("search_matches")(query="x")
    assert "90%+" not in out["upgrade"]["message"]


def test_upgrade_cta_survives_non_numeric_tier(monkeypatch, _fake):
    def fake_get_jobs(params):
        return {"jobs": [], "is_free": True, "total_strong_matches": 150,
                "matches_shown": 50, "strong_90_count": "bad"}
    monkeypatch.setattr(_fake, "get_jobs", fake_get_jobs)
    out = _fn("search_matches")(query="x")  # must not raise
    assert "90%+" not in out["upgrade"]["message"]


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


# --- check_new_matches (high-fit pull tool) ---------------------------------


def _hf_job(score, hours_ago, jid=1):
    disc = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"id": jid, "title": "Eng", "company": "Acme", "score_v2": score, "date_discovered": disc}


def test_check_new_matches_returns_recent_high_fit(monkeypatch, _fake):
    monkeypatch.setattr(_fake, "get_jobs", lambda p: {"jobs": [_hf_job(94, 2, 1), _hf_job(97, 5, 2)]})
    out = _fn("check_new_matches")()
    assert out["count"] == 2
    assert out["new_high_fit"][0]["score"] == 97          # sorted desc
    assert "Radar" in out["upgrade"]["message"]           # conversion pointer


def test_check_new_matches_excludes_old(monkeypatch, _fake):
    monkeypatch.setattr(_fake, "get_jobs", lambda p: {"jobs": [_hf_job(95, 100)]})  # 100h > 48h
    out = _fn("check_new_matches")()
    assert out["count"] == 0 and "No new" in out["message"]
    assert "upgrade" not in out


def test_check_new_matches_excludes_low_score(monkeypatch, _fake):
    monkeypatch.setattr(_fake, "get_jobs", lambda p: {"jobs": [_hf_job(80, 2)]})
    out = _fn("check_new_matches")()
    assert out["count"] == 0


def test_check_new_matches_custom_window(monkeypatch, _fake):
    monkeypatch.setattr(_fake, "get_jobs", lambda p: {"jobs": [_hf_job(92, 100)]})
    out = _fn("check_new_matches")(hours=200)              # widen window to include the 100h-old one
    assert out["count"] == 1


def test_check_new_matches_error_surfaced(monkeypatch, _fake):
    def boom(p):
        raise ApiError(500, "boom")
    monkeypatch.setattr(_fake, "get_jobs", boom)
    out = _fn("check_new_matches")()
    assert out["error"] is True


# --- preferences primitive -------------------------------------------------
#
# The audit that motivated this found agents could SEE why a job matched but not
# REPAIR it: location is the product's #1 complaint class and no MCP tool could
# touch a user's ZIP or radius. The audit named PATCH /api/v1/app-settings as the
# endpoint — that is ADMIN-ONLY and writes GLOBAL settings plus Gmail
# credentials. The correct user-scoped route is PATCH /api/v1/user/settings.

class _PrefsClient:
    def __init__(self):
        self.calls = []

    def get_user_settings(self):
        self.calls.append(("get",))
        return {"compensation_location": {"primary_zip": "77479", "radius_miles": 30.0},
                "gates_hard": {"location_hard_filter": False}}

    def patch_user_settings(self, category, updates):
        self.calls.append(("patch", category, updates))
        return {"status": "success"}


def test_get_preferences_reads_through(monkeypatch):
    fake = _PrefsClient()
    monkeypatch.setattr(server, "_c", lambda: fake)
    out = server.get_preferences()
    assert out["compensation_location"]["primary_zip"] == "77479"
    assert fake.calls == [("get",)]


def test_update_preferences_sends_only_the_changed_key(monkeypatch):
    fake = _PrefsClient()
    monkeypatch.setattr(server, "_c", lambda: fake)
    out = server.update_preferences("gates_hard", {"location_hard_filter": True})
    assert out == {"status": "success"}
    assert fake.calls == [("patch", "gates_hard", {"location_hard_filter": True})]


def test_update_preferences_refuses_untyped_categories(monkeypatch):
    """weights_*/domain_affinities hold free-form maps the route cannot type-check,
    so an agent could quietly reshape the ranker. Refuse before the HTTP call."""
    fake = _PrefsClient()
    monkeypatch.setattr(server, "_c", lambda: fake)
    out = server.update_preferences("weights_title", {"python": 99})
    assert out["error"] == "unsupported_category"
    assert fake.calls == [], "must not reach the API at all"


def test_update_preferences_refuses_an_empty_payload(monkeypatch):
    fake = _PrefsClient()
    monkeypatch.setattr(server, "_c", lambda: fake)
    assert server.update_preferences("gates_hard", {})["error"] == "empty_updates"
    assert fake.calls == []


def test_update_preferences_surfaces_a_422_as_an_error(monkeypatch):
    """An unknown field is rejected server-side; the agent must see that, not a success."""
    class _Boom(_PrefsClient):
        def patch_user_settings(self, category, updates):
            raise ApiError(422, "unknown field 'radius_kilometres'")

    monkeypatch.setattr(server, "_c", lambda: _Boom())
    out = server.update_preferences("compensation_location", {"radius_kilometres": 50})
    assert "error" in out
