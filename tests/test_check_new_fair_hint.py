"""Launch fix 10-31 (Owner; C's cold-start runs): the post's first example prompt is "anything new today?". A brand-new account has Fair matches in its feed and no
Strong or Good ones yet, and check_new_matches answered "No new Strong or Good Matches in the last 48h." for minutes: it reads as broken. When the page the tool already
fetched holds no Strong/Good row at all but does hold Fair rows, say so and say what lifts them. No new API call: the rows are the ones check_new_matches already reads.
Written BEFORE the code: every test that expects the hint fails on package main aed34bd."""
from datetime import datetime, timedelta, timezone

from searchsteward_mcp import server

def hint(n, at_least=False):
    have = f"at least {n}" if at_least else str(n)
    return (f"No Strong or Good matches yet. You have {have} Fair {'match' if n == 1 and not at_least else 'matches'}; "
            "adding your skills (set_skills) or saving your résumé (save_resume) usually lifts the best ones.")


class _Client:
    def __init__(self, jobs):
        self._jobs, self.calls = jobs, []

    def get_jobs(self, params):
        self.calls.append(("get_jobs", params))
        return {"jobs": self._jobs}

    def get_activation_status(self):
        self.calls.append(("get_activation_status",))
        return {"preferences": {"scoring_ready": True}, "target_company_count": 0, "feed_guidance": {"primary_action": "refresh_status"}}


def _job(jid, band, hours_ago=1, score=60, company="Acme"):
    disc = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"id": jid, "title": f"Role {jid}", "company": company, "score_v2": score, "date_discovered": disc, "fit_band_v2": band}


def _call(monkeypatch, jobs, **kw):
    fake = _Client(jobs)
    monkeypatch.setattr(server, "_c", lambda: fake)
    t = server.check_new_matches
    return (t.fn if hasattr(t, "fn") else t)(**kw), fake


def test_fair_only_feed_says_so_plainly(monkeypatch):
    out, _ = _call(monkeypatch, [_job(i, "Fair Match", company=f"Co{i}") for i in range(1, 16)])
    assert out["count"] == 0 and out["new_high_fit"] == []
    assert out["message"] == hint(15)


def test_the_hint_adds_no_api_call(monkeypatch):
    out, fake = _call(monkeypatch, [_job(1, "Fair Match")])
    assert [c[0] for c in fake.calls] == ["get_jobs"] and "set_skills" in out["message"]


def test_a_user_with_a_good_match_never_gets_the_hint_even_when_it_is_old(monkeypatch):
    jobs = [_job(1, "Good Match", hours_ago=500)] + [_job(i, "Fair Match", company=f"Co{i}") for i in range(2, 6)]
    out, _ = _call(monkeypatch, jobs)
    assert out["count"] == 0 and "Fair" not in out["message"] and out["message"].startswith("No new Strong or Good Matches in the last 48h")


def test_a_user_with_a_strong_match_never_gets_the_hint_under_band_strong(monkeypatch):
    jobs = [_job(1, "Strong Match", hours_ago=500)] + [_job(i, "Fair Match", company=f"Co{i}") for i in range(2, 6)]
    out, _ = _call(monkeypatch, jobs, band="Strong")
    assert "Fair" not in out["message"] and "No new Strong Matches" in out["message"]


def test_a_user_with_only_a_good_match_asking_for_strong_keeps_the_try_good_pointer(monkeypatch):
    out, _ = _call(monkeypatch, [_job(1, "Good Match")] + [_job(2, "Fair Match", company="Other")], band="Strong")
    assert "Fair" not in out["message"] and "Try band='Good'" in out["message"]


def test_fresh_strong_good_rows_are_returned_with_no_hint(monkeypatch):
    out, _ = _call(monkeypatch, [_job(1, "Good Match", score=80), _job(2, "Fair Match", company="Other")])
    assert out["count"] == 1 and "message" not in out


def test_no_fair_rows_and_no_strong_good_keeps_the_old_message(monkeypatch):
    out, _ = _call(monkeypatch, [_job(1, "Weak Match"), _job(2, "Poor Match", company="Other")])
    assert out["message"] == "No new Strong or Good Matches in the last 48h." and out["window_hours"] == 48


def test_an_empty_feed_still_says_how_to_start_not_fair(monkeypatch):
    out, _ = _call(monkeypatch, [])
    assert "Fair" not in out.get("message", "")


def test_fair_rows_are_counted_the_way_search_matches_shows_them(monkeypatch):
    jobs = [_job(1, "Fair Match", company="Same"), _job(2, "Fair Match", company="Same"), _job(3, "Fair Match", company="Other")]
    jobs[1]["title"] = jobs[0]["title"]                       # same company + title: one card
    out, _ = _call(monkeypatch, jobs)
    assert out["message"] == hint(2)


def test_a_full_page_of_fair_rows_says_at_least(monkeypatch):
    out, _ = _call(monkeypatch, [_job(i, "Fair Match", company=f"Co{i}") for i in range(1, 26)])
    assert out["message"] == hint(25, at_least=True)


def test_the_window_does_not_matter_to_the_hint(monkeypatch):
    out, _ = _call(monkeypatch, [_job(1, "Fair Match", hours_ago=900)], hours=1)
    assert out["message"] == hint(1) and out["window_hours"] == 1


def test_only_fair_rows_are_counted_when_weaker_rows_are_on_the_page(monkeypatch):
    jobs = [_job(1, "Fair Match", company="A"), _job(2, "Weak Match", company="B"), _job(3, "Poor Match", company="C"), _job(4, "Fair Match", company="D")]
    out, _ = _call(monkeypatch, jobs)
    assert out["message"] == hint(2)
