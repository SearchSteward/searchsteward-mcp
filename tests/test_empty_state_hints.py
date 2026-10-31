"""Cold-start dry run 10-31 (JobSearch ledger c_coldstart_2026-10-31/RESULT.md): a brand-new account got an empty search_matches with no
message, "No new Strong or Good Matches in the last 48h." from check_new_matches (never saying no search is set up), and a profile line that
claimed "only a job title is saved" when nothing was saved. With nothing set up these tools must say how to start."""
from searchsteward_mcp import server
from searchsteward_mcp.client import ApiError

NO_SEARCH = "No search is set up yet. Tell me the job titles and location you want (set_up_search)."
_NEW = {"preferences": {"scoring_ready": False}, "target_company_count": 0, "feed_guidance": {"primary_action": "set_up_search"}}
_NEW_OLD_API = {"preferences": {"scoring_ready": False}, "target_company_count": 0}      # before the API change: no primary_action
_HAS_SEARCH = {"preferences": {"scoring_ready": True}, "target_company_count": 0, "feed_guidance": {"primary_action": "refresh_status"}}


class _Client:
    def __init__(self, jobs=None, status=None, status_error=None, settings=None, resume_text=""):
        self._jobs, self._status, self._status_error, self._settings, self._resume = jobs or [], status, status_error, settings, resume_text
        self.status_calls = 0

    def get_jobs(self, params):
        return {"jobs": self._jobs}

    def get_activation_status(self):
        self.status_calls += 1
        if self._status_error:
            raise self._status_error
        return self._status

    def get_resume(self):
        return {"id": None, "name": None, "text": self._resume}

    def get_user_settings(self):
        return self._settings or {}


def _use(monkeypatch, **kw):
    fake = _Client(**kw)
    monkeypatch.setattr(server, "_c", lambda: fake)
    return fake


def _fn(name):
    t = getattr(server, name)
    return t.fn if hasattr(t, "fn") else t


def test_search_matches_empty_with_no_search_says_how_to_start(monkeypatch):
    for status in (_NEW, _NEW_OLD_API):
        _use(monkeypatch, status=status)
        out = _fn("search_matches")()
        assert out["matches"] == [] and out["count"] == 0
        assert out["message"] == NO_SEARCH


def test_search_matches_filters_or_a_real_search_stay_silent(monkeypatch):
    _use(monkeypatch, status=_NEW)
    assert "message" not in _fn("search_matches")(query="analyst")           # a filter can explain an empty page
    assert "message" not in _fn("search_matches")(page=2)
    _use(monkeypatch, status=_HAS_SEARCH)
    assert "message" not in _fn("search_matches")()                           # they have a search: empty is not "no search"


def test_search_matches_with_rows_makes_no_extra_call(monkeypatch):
    fake = _use(monkeypatch, jobs=[{"id": 1, "title": "Analyst", "company": "Co"}], status=_NEW)
    out = _fn("search_matches")()
    assert out["count"] == 1 and "message" not in out and fake.status_calls == 0


def test_a_failing_status_read_never_breaks_the_empty_answer(monkeypatch):
    _use(monkeypatch, status_error=ApiError(403, "This API key cannot read personal data"))      # a read-only grant
    out = _fn("search_matches")()
    assert out["matches"] == [] and "message" not in out


def test_check_new_matches_with_no_search_says_how_to_start(monkeypatch):
    for band in ("Good", "Strong"):
        _use(monkeypatch, status=_NEW)
        out = _fn("check_new_matches")(band=band)
        assert out["count"] == 0 and out["message"] == NO_SEARCH


def test_check_new_matches_with_a_feed_keeps_its_message(monkeypatch):
    jobs = [{"id": 1, "title": "Analyst", "company": "Co", "fit_band_v2": "Weak Match", "date_discovered": "2020-01-01T00:00:00+00:00"}]
    fake = _use(monkeypatch, jobs=jobs, status=_NEW)
    out = _fn("check_new_matches")()
    assert out["message"] == "No new Strong or Good Matches in the last 48h." and fake.status_calls == 0


def test_profile_line_does_not_claim_a_title_when_none_is_saved(monkeypatch):
    _use(monkeypatch, status={"resume": {"status": "missing"}}, settings={"title_preferences": {"positive_keywords": []}, "weights_jd": {}})
    p = _fn("why_is_my_feed_thin")()["profile"]
    assert "only a job title is saved" not in p["line"]
    assert "Nothing is saved yet" in p["line"] and "set_up_search" in p["line"] and "set_skills" in p["line"]


def test_profile_line_with_a_title_is_unchanged(monkeypatch):
    s = {"title_preferences": {"positive_keywords": ["Senior Data Analyst"]}, "weights_jd": {"body_positive": {"senior data analyst": 3.0}}}
    _use(monkeypatch, status={"first_feed_status": {"state": "ready"}, "resume": {"status": "missing"}}, settings=s)
    assert "only a job title is saved" in _fn("why_is_my_feed_thin")()["profile"]["line"]


# --- delta after Support's review (survivors 4-5) and the weights_title-only profile --------------------------------------------------------

def test_fallback_needs_no_targets_a_target_company_means_a_search_exists(monkeypatch):
    # No primary_action (an API before the chat wording), scoring not ready, but 3 target companies: that is a search.
    status = {"preferences": {"scoring_ready": False}, "target_company_count": 3, "feed_guidance": {}}
    _use(monkeypatch, status=status)
    assert "message" not in _fn("search_matches")()
    _use(monkeypatch, status=status)
    assert _fn("check_new_matches")()["message"] == "No new Strong or Good Matches in the last 48h."


def test_fallback_needs_scoring_ready_to_be_exactly_false(monkeypatch):
    # An unknown readiness (key missing, null) or a ready account with no targets is not "no search".
    for prefs in ({}, {"scoring_ready": None}, {"scoring_ready": True}, {"scoring_ready": 1}):
        _use(monkeypatch, status={"preferences": prefs, "target_company_count": 0})
        assert "message" not in _fn("search_matches")(), prefs


def test_fallback_says_no_search_when_not_ready_and_no_targets(monkeypatch):
    for count in (0, None):
        _use(monkeypatch, status={"preferences": {"scoring_ready": False}, "target_company_count": count})
        assert _fn("search_matches")()["message"] == NO_SEARCH


def test_profile_with_only_weights_title_does_not_say_no_title(monkeypatch):
    # scoring_ready is true for a weights_title-only account (has_positive_anchor); the profile line must not claim "no job title".
    s = {"title_preferences": {"positive_keywords": []}, "weights_title": {"data analyst": 3.0}, "weights_jd": {}}
    _use(monkeypatch, status={"first_feed_status": {"state": "ready"}, "resume": {"status": "missing"}}, settings=s)
    line = _fn("why_is_my_feed_thin")()["profile"]["line"]
    assert "Nothing is saved yet" not in line and "only a job title is saved" in line
