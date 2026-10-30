"""save_resume and set_skills (Owner top priority 10-30: Eric asked claude.ai to update his profile and it could change titles and location but
could not save the résumé or the skills). A stateful fake API stands in for the app: it keeps one primary résumé and one settings blob, so
every assertion is about what a later get_resume / get_preferences returns, and about what was NOT written."""
import copy

import pytest

from searchsteward_mcp import server
from searchsteward_mcp.client import ApiError

RESUME = ("Invented Person. Senior data engineer with eight years of dbt, Snowflake and SQL. Built warehouse pipelines for an invented retailer. "
          "Led a team of four. Invented University, BSc. " * 2)


class _Api:
    """get_resume returns the primary; the first created résumé becomes primary (as ats_db does); PATCH needs an id."""

    def __init__(self, resume=None, settings=None):
        self.resume = resume                       # {"id", "name", "text"} or None
        self.settings = settings if settings is not None else {}
        self.calls = []

    def get_resume(self):
        return dict(self.resume) if self.resume else {"id": None, "name": None, "text": ""}

    def create_resume_profile(self, name, text):
        self.calls.append(("create", name, text))
        self.resume = {"id": 7, "name": name, "text": text}          # the first profile becomes primary
        return {"status": "success", "id": 7}

    def update_resume_profile(self, profile_id, text, name=None):
        self.calls.append(("update", profile_id, text, name))
        self.resume = {**self.resume, "text": text, **({"name": name} if name else {})}
        return {"status": "success"}

    def get_user_settings(self):
        return copy.deepcopy(self.settings)

    def patch_user_settings(self, category, updates):
        self.calls.append(("patch", category, copy.deepcopy(updates)))
        cat = dict(self.settings.get(category) or {})
        cat.update(copy.deepcopy(updates))                             # per-key upsert, like SettingsService
        self.settings[category] = cat
        return {"status": "success"}


def _use(monkeypatch, **kw):
    api = _Api(**kw)
    monkeypatch.setattr(server, "_c", lambda: api)
    return api


def _fn(name):
    t = getattr(server, name)
    return t.fn if hasattr(t, "fn") else t


BASE = {"title_preferences": {"positive_keywords": ["Senior Data Analyst", "Analytics Engineer"]},
        "weights_jd": {"body_positive": {"senior data analyst": 3.0, "python": 9}, "body_negative": {"junior": 5}},
        "gates_hard": {"location_policy": "any"}, "thresholds": {"watch": 40}}


def _settings():
    return copy.deepcopy(BASE)


# ---------------------------------------------------------------- save_resume
def test_save_resume_creates_the_first_resume_and_get_resume_returns_it(monkeypatch):
    api = _use(monkeypatch)
    out = _fn("save_resume")(RESUME)
    assert out["saved"] is True and out["created"] is True and out["characters"] == len(RESUME.strip())
    assert api.calls == [("create", "Resume", RESUME.strip())]
    assert _fn("get_resume")()["text"] == RESUME.strip()                # the primary, read back through the tool


def test_save_resume_updates_the_existing_primary_by_id_and_keeps_its_name(monkeypatch):
    api = _use(monkeypatch, resume={"id": 5, "name": "My CV", "text": "old " * 80})
    out = _fn("save_resume")(RESUME)
    assert out["saved"] is True and out["created"] is False and out["name"] == "My CV"
    assert api.calls == [("update", 5, RESUME.strip(), None)]            # id + text only: no name sent unless given
    assert _fn("get_resume")()["text"] == RESUME.strip()


def test_save_resume_sends_a_new_name_only_when_asked(monkeypatch):
    api = _use(monkeypatch, resume={"id": 5, "name": "My CV", "text": "old " * 80})
    out = _fn("save_resume")(RESUME, name="  Data CV ")
    assert api.calls == [("update", 5, RESUME.strip(), "Data CV")] and out["name"] == "Data CV"


@pytest.mark.parametrize("text", ["", "   ", "Senior engineer.", "x" * 199])
def test_save_resume_refuses_short_text_and_touches_nothing(monkeypatch, text):
    api = _use(monkeypatch, resume={"id": 5, "name": "My CV", "text": "old " * 80})
    out = _fn("save_resume")(text)
    assert out["error"] == "resume_too_short" and "summary" in out["detail"]
    assert api.calls == [] and api.resume["text"] == "old " * 80         # the saved résumé is untouched


def test_save_resume_accepts_exactly_the_floor(monkeypatch):
    api = _use(monkeypatch)
    assert _fn("save_resume")("x" * 200)["saved"] is True and api.resume["text"] == "x" * 200


def test_save_resume_does_not_claim_a_save_the_api_did_not_make(monkeypatch):
    api = _use(monkeypatch)
    api.create_resume_profile = lambda name, text: {"status": "success", "id": 9}      # accepted, but not primary / not readable back
    out = _fn("save_resume")(RESUME)
    assert out["error"] == "resume_not_saved_as_primary" and "saved" not in out


def test_save_resume_maps_api_errors(monkeypatch):
    api = _use(monkeypatch)

    def boom(*a, **k):
        raise ApiError(403, "This API key cannot write")
    api.create_resume_profile = boom
    out = _fn("save_resume")(RESUME)
    assert out["error"] is True and out["status"] == 403


def test_save_resume_docstring_says_it_does_not_change_scores_and_points_at_set_skills():
    doc = _fn("save_resume").__doc__
    assert "does not change" in doc.lower() or "does not" in doc.lower() and "scores" in doc.lower()
    assert "set_skills" in doc and "tailoring" in doc and "FULL" in doc and "Confirm with the user" in doc


# ---------------------------------------------------------------- set_skills
def test_add_merges_with_the_existing_terms_and_changes_nothing_else(monkeypatch):
    api = _use(monkeypatch, settings=_settings())
    before = _settings()
    out = _fn("set_skills")(["dbt", "Snowflake", "SQL"])
    assert out["saved"] is True and out["added"] == ["dbt", "snowflake", "sql"] and out["kept_existing"] == []
    after = api.settings
    bp = after["weights_jd"]["body_positive"]
    assert bp == {"senior data analyst": 3.0, "python": 9, "dbt": 12, "snowflake": 11, "sql": 10}         # weight = max(5, 12 - idx) by ARGUMENT position
    for cat in before:                                                  # everything but body_positive is byte-equal
        for k, v in before[cat].items():
            if (cat, k) != ("weights_jd", "body_positive"):
                assert after[cat][k] == v, (cat, k)
    assert [c for c in api.calls if c[0] == "patch"] == [("patch", "weights_jd", {"body_positive": bp})]   # ONE write, ONE key, ONE category


def test_add_keeps_an_existing_skills_old_weight(monkeypatch):
    api = _use(monkeypatch, settings=_settings())
    out = _fn("set_skills")(["Python", "dbt"])
    bp = api.settings["weights_jd"]["body_positive"]
    assert bp["python"] == 9 and bp["dbt"] == 11 and out["kept_existing"] == ["python"] and out["added"] == ["dbt"]


def test_add_with_nothing_new_writes_nothing_and_does_not_rescore(monkeypatch):
    api = _use(monkeypatch, settings=_settings())
    out = _fn("set_skills")(["PYTHON", "python", "Senior Data Analyst"])
    assert out["saved"] is False and out["reason"] == "nothing_new"
    assert not [c for c in api.calls if c[0] == "patch"]


def test_terms_that_are_only_a_target_title_are_dropped_and_reported(monkeypatch):
    api = _use(monkeypatch, settings=_settings())
    out = _fn("set_skills")(["Analytics-Engineer", "analytics engineer", "dbt"])        # -/space variants of a title, duplicated
    assert out["dropped_as_titles"] == ["analytics-engineer"] and out["added"] == ["dbt"]
    assert "analytics-engineer" not in api.settings["weights_jd"]["body_positive"]
    assert api.settings["weights_jd"]["body_positive"]["dbt"] == 10                       # idx 2: a dropped term leaves its slot


def test_the_list_is_capped_at_15_and_extras_are_reported(monkeypatch):
    api = _use(monkeypatch, settings={"title_preferences": {"positive_keywords": []}, "weights_jd": {"body_positive": {}}})
    terms = [f"skill{i:02d}" for i in range(20)]
    out = _fn("set_skills")(terms)
    bp = api.settings["weights_jd"]["body_positive"]
    assert len(bp) == 15 and out["dropped_over_limit"] == terms[15:]
    assert bp["skill00"] == 12 and bp["skill06"] == 6 and bp["skill07"] == 5 and bp["skill14"] == 5   # floor 5


def test_terms_are_normalised_and_deduplicated(monkeypatch):
    api = _use(monkeypatch, settings={"title_preferences": {"positive_keywords": []}, "weights_jd": {"body_positive": {}}})
    _fn("set_skills")(["  Power   BI ", "power-bi", "", "   ", "SQL"])
    assert api.settings["weights_jd"]["body_positive"] == {"power bi": 12, "sql": 8}   # idx 0 and 4; the duplicate and blanks are skipped


def test_replace_makes_the_list_the_skills_but_keeps_target_title_terms(monkeypatch):
    api = _use(monkeypatch, settings=_settings())
    out = _fn("set_skills")(["dbt", "Snowflake"], mode="replace")
    assert api.settings["weights_jd"]["body_positive"] == {"senior data analyst": 3.0, "dbt": 12, "snowflake": 11}   # python is gone
    assert out["saved"] is True and out["mode"] == "replace"


def test_replace_with_nothing_usable_never_empties_the_skills(monkeypatch):
    api = _use(monkeypatch, settings=_settings())
    out = _fn("set_skills")(["Senior Data Analyst", " "], mode="replace")
    assert out["error"] == "no_usable_skills"
    assert not [c for c in api.calls if c[0] == "patch"] and api.settings["weights_jd"]["body_positive"]["python"] == 9


def test_add_mode_is_not_replace_mode(monkeypatch):
    """The semantics that must differ: add keeps python, replace drops it."""
    a = _use(monkeypatch, settings=_settings())
    _fn("set_skills")(["dbt"], mode="add")
    assert "python" in a.settings["weights_jd"]["body_positive"]
    b = _use(monkeypatch, settings=_settings())
    _fn("set_skills")(["dbt"], mode="replace")
    assert "python" not in b.settings["weights_jd"]["body_positive"]


def test_a_bad_mode_is_refused(monkeypatch):
    api = _use(monkeypatch, settings=_settings())
    assert _fn("set_skills")(["dbt"], mode="merge")["error"] == "bad_mode"
    assert not api.calls


def test_set_skills_does_not_claim_a_save_the_api_did_not_make(monkeypatch):
    api = _use(monkeypatch, settings=_settings())
    api.patch_user_settings = lambda category, updates: {"status": "success"}              # accepted but nothing stored
    out = _fn("set_skills")(["dbt"])
    assert out["error"] == "skills_not_saved_as_sent" and "saved" not in out


def test_set_skills_maps_api_errors(monkeypatch):
    api = _use(monkeypatch, settings=_settings())

    def boom(category, updates):
        raise ApiError(402, "upgrade")
    api.patch_user_settings = boom
    out = _fn("set_skills")(["dbt"])
    assert out["error"] is True and out["status"] == 402


def test_set_skills_docstring_says_it_rescores_the_whole_feed_and_to_confirm():
    doc = _fn("set_skills").__doc__
    assert "RE-SCORES THE WHOLE FEED" in doc and "CONFIRM" in doc and "get_preferences" in doc


def test_the_mode_is_an_enum_in_the_tool_schema():
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    props = tools["set_skills"].parameters["properties"]
    assert props["mode"].get("enum") == ["add", "replace"] and props["skills"]["type"] == "array"
    assert {"save_resume", "set_skills"} <= set(tools)


# ---------------------------------------------------------------- the copy (Owner point 3) and the four existing categories (acceptance d)
class _ThinClient:
    def get_resume(self):
        return {"id": None, "name": None, "text": ""}

    def get_activation_status(self):
        return {"first_feed_status": {"state": "ready", "feed_count": 22, "strong_count": 0}, "thin_feed_threshold": 25, "thin_feed_strong_min": 5,
                "resume": {"status": "missing"}}

    def get_user_settings(self):
        return {"title_preferences": {"positive_keywords": ["Senior Data Analyst"]}, "weights_jd": {"body_positive": {"senior data analyst": 3.0}}}


def test_the_thin_feed_line_points_at_set_skills_not_at_a_resume_upload(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _ThinClient())
    line = _fn("why_is_my_feed_thin")()["profile"]["line"]
    assert "set_skills" in line and "update_preferences" in line
    assert "only a job title is saved" in line
    assert "does not change scores" in line                                # a saved résumé changes no score
    for stale in ("Settings", "in the app", "Add your r"):
        assert stale not in line, stale


def test_update_preferences_still_writes_only_the_four_categories_and_points_at_the_new_tools(monkeypatch):
    assert server._WRITABLE_PREFERENCE_CATEGORIES == {"compensation_location", "gates_hard", "title_preferences", "thresholds"}
    api = _use(monkeypatch, settings=_settings())
    out = _fn("update_preferences")("weights_jd", {"body_positive": {"x": 1}})
    assert out["error"] == "unsupported_category" and not api.calls
    doc = _fn("update_preferences").__doc__
    assert "set_skills" in doc and "save_resume" in doc
