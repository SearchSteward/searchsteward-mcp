"""Fixes from Eric's MCP demo session (10-28): get_job printed raw 48 where the list printed 58, a remote
row read "Chapel Hill, NC" in a 30-mile Denver feed, set_up_search promised "$120,000+" for a floor
that hides nothing, and why_is_my_feed_thin never said the profile (one title, no résumé) was the cause."""
from searchsteward_mcp import server
from searchsteward_mcp.client import ApiError


class _Client:
    def __init__(self, jobs=None, ctx=None, status=None, settings=None):
        self._jobs, self._ctx, self._status, self._settings = jobs, ctx, status, settings

    def get_jobs(self, params):
        return {"jobs": self._jobs or []}

    def get_job_context(self, job_id):
        return dict(self._ctx)

    def get_activation_status(self):
        return self._status

    def get_user_settings(self):
        if self._settings is None:
            raise ApiError(403, "This API key cannot read personal data")
        return self._settings


def _use(monkeypatch, **kw):
    fake = _Client(**kw)
    monkeypatch.setattr(server, "_c", lambda: fake)
    return fake


def _fn(name):
    t = getattr(server, name)
    return t.fn if hasattr(t, "fn") else t


def test_row_marks_remote(monkeypatch):
    _use(monkeypatch, jobs=[{"id": 1, "location": "Chapel Hill, NC, US", "is_remote": 1},
                            {"id": 2, "location": "Denver, CO, US", "is_remote": 0},
                            {"id": 3, "location": "Austin, TX"}])
    rows = _fn("search_matches")()["matches"]
    assert [r["remote"] for r in rows] == [True, False, None]


def test_get_job_prints_the_list_number(monkeypatch):
    _use(monkeypatch, ctx={"id": 7, "score": 48.0, "final_score": 48.0, "display_score": 58})
    out = _fn("get_job")(job_id=7)
    assert out["score"] == 58 and out["score_raw"] == 48.0 and out["final_score"] == 48.0


def test_get_job_on_an_older_backend_is_unchanged(monkeypatch):
    _use(monkeypatch, ctx={"id": 7, "score": 48.0})
    out = _fn("get_job")(job_id=7)
    assert out["score"] == 48.0 and "score_raw" not in out


def test_summary_does_not_promise_a_hard_floor():
    s = server._criteria_summary({"title_includes": ["Senior Data Analyst"], "location": "Denver, CO",
                                  "radius_miles": 30, "salary_min": 120000})
    assert s.endswith("$120,000 pay floor saved (lower-paid jobs still show)")
    assert "$120,000+" not in s


_THIN = {"first_feed_status": {"state": "ready", "feed_count": 22, "strong_count": 0},
         "thin_feed_threshold": 25, "thin_feed_strong_min": 5}
_TITLE_ONLY = {"title_preferences": {"positive_keywords": ["Senior Data Analyst"]},
               "weights_jd": {"body_positive": {"senior data analyst": 3.0}}}


def _profile():
    p = _fn("why_is_my_feed_thin")().get("profile")
    assert p is not None, "why_is_my_feed_thin returned no profile"
    return p


def test_title_only_profile_gets_the_line(monkeypatch):
    _use(monkeypatch, status={**_THIN, "resume": {"status": "missing"}}, settings=_TITLE_ONLY)
    p = _profile()
    assert p["resume"] == "missing" and p["skills_saved"] == 0
    assert "only a job title is saved" in p["line"]


def test_two_skills_still_gets_the_line(monkeypatch):
    s = {**_TITLE_ONLY, "weights_jd": {"body_positive": {"senior data analyst": 3, "sql": 2, "tableau": 2}}}
    _use(monkeypatch, status={**_THIN, "resume": {"status": "skipped"}}, settings=s)
    assert "line" in _profile()


def test_three_skills_no_line(monkeypatch):
    s = {**_TITLE_ONLY, "weights_jd": {"body_positive": {"sql": 2, "tableau": 2, "python": 2}}}
    _use(monkeypatch, status={**_THIN, "resume": {"status": "missing"}}, settings=s)
    p = _profile()
    assert p["skills_saved"] == 3 and "line" not in p


def test_imported_resume_no_line(monkeypatch):
    _use(monkeypatch, status={**_THIN, "resume": {"status": "imported"}}, settings=_TITLE_ONLY)
    assert "line" not in _profile()


def test_unreadable_settings_make_no_claim(monkeypatch):
    _use(monkeypatch, status={**_THIN, "resume": {"status": "missing"}}, settings=None)
    p = _profile()
    assert p["skills_saved"] is None and "line" not in p
