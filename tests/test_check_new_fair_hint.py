"""Launch fix 10-31 (Owner; C's cold-start runs): the post's first example prompt is "anything new today?". A brand-new account has Fair matches in its feed and no
Strong or Good ones yet, and check_new_matches used to answer "No new Strong or Good Matches in the last 48h." for minutes: it reads as broken. When the page the tool
already fetched holds no Strong/Good row at all but does hold Fair rows, say so and say what lifts them. No new API call: the rows are the ones check_new_matches reads.
Follow-up (Owner, prod run 3): the first wording counted folded cards ("You have 4 Fair matches" while search_matches/feed_count showed 15 rows). The hint now makes NO
claim about a total: no number at all, so it cannot disagree with any other count."""
import re
from datetime import datetime, timedelta, timezone

from searchsteward_mcp import server

HINT = ("No Strong or Good matches yet: your best matches so far are Fair. "
        "Adding your skills (set_skills) or saving your résumé (save_resume) usually lifts them.")


class _Client:
    def __init__(self, jobs):
        self._jobs, self.calls = jobs, []

    def get_jobs(self, params):
        self.calls.append(("get_jobs", params))
        return {"jobs": self._jobs}

    def get_activation_status(self):
        self.calls.append(("get_activation_status",))
        return {"preferences": {"scoring_ready": True}, "target_company_count": 0, "feed_guidance": {"primary_action": "refresh_status"}}


def _job(jid, band, hours_ago=1, score=60, company="Acme", title=None):
    disc = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"id": jid, "title": title or f"Role {jid}", "company": company, "score_v2": score, "date_discovered": disc, "fit_band_v2": band}


def _call(monkeypatch, jobs, **kw):
    fake = _Client(jobs)
    monkeypatch.setattr(server, "_c", lambda: fake)
    t = server.check_new_matches
    return (t.fn if hasattr(t, "fn") else t)(**kw), fake


def test_fair_only_feed_says_so_plainly(monkeypatch):
    out, _ = _call(monkeypatch, [_job(i, "Fair Match", company=f"Co{i}") for i in range(1, 16)])
    assert out["count"] == 0 and out["new_high_fit"] == []
    assert out["message"] == HINT


def test_the_same_text_for_1_4_15_and_25_rows(monkeypatch):
    for n in (1, 4, 15, 25):
        out, _ = _call(monkeypatch, [_job(i, "Fair Match", company=f"Co{i}") for i in range(1, n + 1)])
        assert out["message"] == HINT, n


def test_the_same_text_whether_the_rows_fold_into_few_cards_or_not(monkeypatch):
    same = [_job(i, "Fair Match", company="Kohls", title="Analyst") for i in range(1, 16)]          # 15 rows, 1 card
    out, _ = _call(monkeypatch, same)
    assert out["message"] == HINT


def test_no_digit_appears_in_the_hint(monkeypatch):
    for n in (1, 4, 15, 25):
        out, _ = _call(monkeypatch, [_job(i, "Fair Match", company=f"Co{i}") for i in range(1, n + 1)])
        assert not re.search(r"\d", out["message"]), out["message"]


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


def test_an_empty_feed_keeps_the_no_search_line_from_29(monkeypatch):
    class _NoSearch(_Client):
        def get_activation_status(self):
            return {"preferences": {"scoring_ready": False}, "target_company_count": 0, "feed_guidance": {"primary_action": "set_up_search"}}
    fake = _NoSearch([])
    monkeypatch.setattr(server, "_c", lambda: fake)
    t = server.check_new_matches
    out = (t.fn if hasattr(t, "fn") else t)()
    assert out["message"] == server._NO_SEARCH_MESSAGE and "Fair" not in out["message"]


def test_an_empty_feed_with_a_search_set_up_says_no_fair_either(monkeypatch):
    out, _ = _call(monkeypatch, [])
    assert "Fair" not in out.get("message", "")


def test_the_window_does_not_matter_to_the_hint(monkeypatch):
    out, _ = _call(monkeypatch, [_job(1, "Fair Match", hours_ago=900)], hours=1)
    assert out["message"] == HINT and out["window_hours"] == 1
