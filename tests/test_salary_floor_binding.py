"""M4 (salary-bind B, Eric 10-29): set_up_search's floor now hides lower-paid jobs, so the copy says so,
a likely extra zero is checked with the user, and salary_confirmed carries their answer to the API."""
from searchsteward_mcp import server
from searchsteward_mcp.client import ApiError


class _Rec:
    def __init__(self, out=None, exc=None):
        self.bodies, self._out, self._exc = [], out or {"applied": True, "feed_pending": True}, exc

    def preferences_from_search(self, body):
        self.bodies.append(body)
        if self._exc:
            raise self._exc
        return self._out


def _use(monkeypatch, rec):
    monkeypatch.setattr(server, "_c", lambda: rec)
    return rec


def test_preview_checks_a_floor_above_400k_with_the_user(monkeypatch):
    rec = _use(monkeypatch, _Rec())
    out = server.set_up_search(["Data Analyst"], salary_min=1_200_000)
    assert out["saved"] is False and rec.bodies == []
    assert "Did the user mean $120,000?" in out.get("note", "")
    assert "salary_confirmed=True" in out["note"]


def test_preview_says_nothing_at_400k_or_once_confirmed(monkeypatch):
    _use(monkeypatch, _Rec())
    assert "note" not in server.set_up_search(["Data Analyst"], salary_min=400_000)
    assert "note" not in server.set_up_search(["Data Analyst"], salary_min=1_200_000, salary_confirmed=True)


def test_the_radius_note_and_the_floor_note_both_survive(monkeypatch):
    _use(monkeypatch, _Rec())
    out = server.set_up_search(["Data Analyst"], location="Denver, CO", radius_miles=900, salary_min=1_200_000)
    assert "commute radius tops out" in out["note"] and "Did the user mean" in out["note"]


def test_confirm_sends_salary_confirmed_only_when_the_user_confirmed(monkeypatch):
    rec = _use(monkeypatch, _Rec())
    server.set_up_search(["Data Analyst"], salary_min=500_000, confirm=True, salary_confirmed=True)
    server.set_up_search(["Data Analyst"], salary_min=90_000, confirm=True)
    assert rec.bodies[0].get("salary_confirmed") is True
    assert "salary_confirmed" not in rec.bodies[1]


def test_salary_confirmed_is_not_shown_as_a_saved_criterion(monkeypatch):
    _use(monkeypatch, _Rec())
    out = server.set_up_search(["Data Analyst"], salary_min=500_000, confirm=True, salary_confirmed=True)
    assert out["saved"] is True and "salary_confirmed" not in out["criteria"]


def test_the_api_refusal_reaches_the_agent(monkeypatch):
    detail = '{"error": "salary_floor_implausible", "suggestion": 120000}'
    _use(monkeypatch, _Rec(exc=ApiError(422, detail)))
    out = server.set_up_search(["Data Analyst"], salary_min=1_200_000, confirm=True)
    assert out["error"] is True and out["status"] == 422 and "salary_floor_implausible" in out["detail"]
