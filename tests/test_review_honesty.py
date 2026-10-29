"""review_candidates / submit_match_verdict say when nothing was scored; search_matches errors name the TOOL argument, not the API field.

Gemini blind QA 10-29 (MEDIUM): on a fresh account every candidate is evaluated:false / score_v2 null, so a verdict is stored with score_at_label null while the
tool text promised "the score the job had at the time". Unscored-first is deliberate (the ranker's silences), so the fix is honesty, not a different list.
Plus (Owner 10-29): the dismissed-view 422 named min_compensation_usd, an internal field, instead of salary_min.
"""
import json

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from searchsteward_mcp import server
from searchsteward_mcp.client import ApiError


async def _with_session(fn):
    srv = server.mcp._mcp_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: srv.run(server_streams[0], server_streams[1], srv.create_initialization_options()))
            try:
                async with ClientSession(client_streams[0], client_streams[1]) as session:
                    await session.initialize()
                    return await fn(session)
            finally:
                tg.cancel_scope.cancel()


def _call(tool, args=None):
    async def go(s):
        return await s.call_tool(tool, args or {})
    return anyio.run(_with_session, go)


def _cand(i, evaluated, score=None):
    return {"job_id": i, "company": "Co", "role": f"Role {i}", "location": "X", "is_remote": 0, "url": f"https://x/{i}",
            "score_v2": score, "fit_band": "Good Match" if evaluated else None, "decision_reason": None, "evaluated": evaluated, "existing_verdict": None}


class _Review:
    def __init__(self, candidates=None, verdict=None, exc=None):
        self.candidates, self.verdict, self.exc = candidates, verdict, exc

    def get_review_candidates(self, params):
        if self.exc:
            raise self.exc
        return {"candidates": self.candidates}

    def submit_match_verdict(self, job_id, verdict, note=None):
        if self.exc:
            raise self.exc
        return self.verdict


def _body(res):
    return json.loads(res.content[0].text)


def test_an_all_unscored_page_says_so(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Review([_cand(1, False), _cand(2, False), _cand(3, False)]))
    res = _call("review_candidates")
    b = _body(res)
    assert res.isError is False
    assert b["summary"]["scored"] == 0 and b["summary"]["unscored"] == 3
    assert "none of these had a score on file for you" in b["summary"]["note"].lower() and "score_at_label will be null" in b["summary"]["note"]


def test_a_mixed_page_has_counts_and_no_note(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Review([_cand(1, False), _cand(2, True, 71.0), _cand(3, True, 64.0)]))
    b = _body(_call("review_candidates"))
    assert b["summary"] == {"scored": 2, "unscored": 1}


def test_an_empty_page_has_no_note(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Review([]))
    b = _body(_call("review_candidates"))
    assert b["summary"] == {"scored": 0, "unscored": 0} and "note" not in b["summary"]


def test_candidates_are_neither_reordered_nor_filtered(monkeypatch):
    cands = [_cand(5, False), _cand(2, True, 80.0), _cand(9, False), _cand(1, True, 50.0)]
    monkeypatch.setattr(server, "_c", lambda: _Review(cands))
    assert [c["job_id"] for c in _body(_call("review_candidates"))["candidates"]] == [5, 2, 9, 1]


def test_a_review_error_is_untouched(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Review(exc=ApiError(500, "down")))
    res = _call("review_candidates")
    assert res.isError is True and _body(res) == {"error": True, "status": 500, "detail": "down"}


def test_a_verdict_on_an_unscored_job_says_the_score_is_missing(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Review(verdict={"ok": True, "job_id": 7, "verdict": "should_surface", "score_at_label": None, "fit_band_at_label": None}))
    b = _body(_call("submit_match_verdict", {"job_id": 7, "verdict": "should_surface"}))
    assert b["scored"] is False and "no score on file for you" in b["note"] and "saved without a score" in b["note"]
    assert b["ok"] is True and b["job_id"] == 7 and b["verdict"] == "should_surface" and b["score_at_label"] is None   # backend fields verbatim


def test_a_verdict_on_a_scored_job_is_marked_scored_and_has_no_note(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Review(verdict={"ok": True, "job_id": 7, "verdict": "unsure", "score_at_label": 55.9, "fit_band_at_label": "Good Match"}))
    b = _body(_call("submit_match_verdict", {"job_id": 7, "verdict": "unsure"}))
    assert b["scored"] is True and "note" not in b and b["score_at_label"] == 55.9 and b["fit_band_at_label"] == "Good Match"


def test_a_score_of_zero_counts_as_scored(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Review(verdict={"ok": True, "job_id": 7, "verdict": "unsure", "score_at_label": 0, "fit_band_at_label": "Filtered Out"}))
    assert _body(_call("submit_match_verdict", {"job_id": 7, "verdict": "unsure"}))["scored"] is True


def test_the_tool_descriptions_state_the_limit():
    async def go(s):
        return {t.name: t.description for t in (await s.list_tools()).tools}
    d = {k: " ".join(v.split()) for k, v in anyio.run(_with_session, go).items()}   # docstring line wraps are not part of the text
    assert "already has scored matches" in d["review_candidates"] and "If it says none, use search_matches first" in d["review_candidates"]
    assert "almost nothing" not in d["review_candidates"]   # Codex: an unproven claim about new accounts
    assert "null when no score was on file for you for that job" in d["submit_match_verdict"]


class _Jobs:
    def __init__(self, exc):
        self.exc = exc

    def get_jobs(self, params):
        raise self.exc


def test_a_422_names_the_tool_argument_not_the_api_field(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Jobs(ApiError(422, "min_compensation_usd is not allowed with bucket=dismissed")))
    res = _call("search_matches", {"salary_min": 100000, "status": "dismissed"})
    b = _body(res)
    assert res.isError is True and b["status"] == 422
    assert b["detail"] == "salary_min is not allowed with status=dismissed"
    assert "min_compensation_usd" not in res.content[0].text and "bucket" not in res.content[0].text


def test_other_error_text_is_byte_identical(monkeypatch):
    monkeypatch.setattr(server, "_c", lambda: _Jobs(ApiError(500, "database unavailable, try later")))
    assert _body(_call("search_matches"))["detail"] == "database unavailable, try later"


def test_a_backend_payload_without_a_candidates_list_is_returned_unchanged(monkeypatch):
    class _Odd:
        def get_review_candidates(self, params):
            return {"unexpected": 1}
    monkeypatch.setattr(server, "_c", lambda: _Odd())
    assert _body(_call("review_candidates")) == {"unexpected": 1}


# ---- Codex review of 8ecbdb3 ----

def test_scored_is_the_evaluated_flag_not_the_score(monkeypatch):
    cands = [_cand(1, True, None), _cand(2, True, None), _cand(3, False, 99.0)]   # evaluated with no score counts as scored; a stray score on an unevaluated row does not
    monkeypatch.setattr(server, "_c", lambda: _Review(cands))
    assert _body(_call("review_candidates"))["summary"] == {"scored": 2, "unscored": 1}


def test_every_candidate_field_passes_through_untouched(monkeypatch):
    cands = [{**_cand(5, False), "extra": {"k": [1, 2]}}, _cand(2, True, 80.0)]
    monkeypatch.setattr(server, "_c", lambda: _Review(cands))
    assert _body(_call("review_candidates"))["candidates"] == cands


def test_verdict_fields_pass_through_verbatim_with_extras(monkeypatch):
    back = {"ok": True, "job_id": 7, "verdict": "unsure", "score_at_label": 55.9, "fit_band_at_label": "Good Match", "future_field": {"a": 1}}
    monkeypatch.setattr(server, "_c", lambda: _Review(verdict=back))
    assert _body(_call("submit_match_verdict", {"job_id": 7, "verdict": "unsure"})) == {**back, "scored": True}


@pytest.mark.parametrize("score", [0, 0.0])
def test_a_zero_score_adds_only_the_scored_flag(monkeypatch, score):
    back = {"ok": True, "job_id": 7, "verdict": "unsure", "score_at_label": score, "fit_band_at_label": "Filtered Out"}
    monkeypatch.setattr(server, "_c", lambda: _Review(verdict=back))
    assert _body(_call("submit_match_verdict", {"job_id": 7, "verdict": "unsure"})) == {**back, "scored": True}


def test_a_payload_without_score_at_label_is_left_alone(monkeypatch):
    back = {"ok": True, "job_id": 7, "verdict": "unsure"}
    monkeypatch.setattr(server, "_c", lambda: _Review(verdict=back))
    assert _body(_call("submit_match_verdict", {"job_id": 7, "verdict": "unsure"})) == back


def test_a_backend_scored_field_and_note_are_not_overwritten(monkeypatch):
    back = {"ok": True, "job_id": 7, "verdict": "unsure", "score_at_label": None, "fit_band_at_label": None, "scored": "backend-says", "note": "from backend"}
    monkeypatch.setattr(server, "_c", lambda: _Review(verdict=back))
    b = _body(_call("submit_match_verdict", {"job_id": 7, "verdict": "unsure"}))
    assert b["scored"] == "backend-says" and b["note"].startswith("from backend ") and "no score on file for you" in b["note"]


@pytest.mark.parametrize("detail", ["bucketed rows", "3 buckets full", "min_compensation_usd_extra is odd", "subbucket", "x_bucket_y"])
def test_longer_words_and_identifiers_are_not_rewritten(monkeypatch, detail):
    monkeypatch.setattr(server, "_c", lambda: _Jobs(ApiError(422, detail)))
    assert _body(_call("search_matches"))["detail"] == detail


@pytest.mark.parametrize("raw,want", [("bucket=dismissed", "status=dismissed"), ("(min_compensation_usd)", "(salary_min)"),
                                      ("min_compensation_usd, bucket", "salary_min, status"), ("'bucket'", "'status'"), ("bucket.", "status.")])
def test_standalone_field_names_are_rewritten_through_punctuation(monkeypatch, raw, want):
    monkeypatch.setattr(server, "_c", lambda: _Jobs(ApiError(422, raw)))
    assert _body(_call("search_matches"))["detail"] == want


def test_the_translation_runs_only_in_search_matches(monkeypatch):
    class _Upd:
        def patch_application(self, aid, body):
            raise ApiError(422, "bucket min_compensation_usd")
    monkeypatch.setattr(server, "_c", lambda: _Upd())
    res = _call("update_application", {"application_id": 3, "status": "offer"})
    assert res.isError is True and _body(res)["status_error"]["detail"] == "bucket min_compensation_usd"
