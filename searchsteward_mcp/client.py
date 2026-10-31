"""Thin HTTP client for the SearchSteward REST API.

Holds no business logic — every method maps to one REST endpoint the PAT is
allowed to reach (see the server-side allowlist in api_service.py). Auth is a
personal access token (`ss_pat_…`) sent as a Bearer credential.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

DEFAULT_BASE = "https://searchsteward.com"


class ConfigError(RuntimeError):
    """Raised when required configuration (API key / base URL) is missing or unsafe."""


class ApiError(RuntimeError):
    """A non-2xx response from the API. Carries the status and best-effort detail."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


def _require_https(base_url: str, internal: bool = False) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    is_local = host in {"localhost", "127.0.0.1", "::1"}
    # The hosted connector calls the API container directly on a private compose network. Only an explicit
    # `internal=True` (never an env var) opens plain http, and only to the single host `api`.
    is_internal = internal and parsed.scheme == "http" and host == "api"
    if parsed.scheme != "https" and not is_local and not is_internal:
        raise ConfigError(
            f"SEARCHSTEWARD_API_BASE must use https (got {base_url!r}); the API key "
            "would otherwise travel in cleartext. localhost is exempt for local dev."
        )
    return base_url.rstrip("/")


class SearchStewardClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        internal: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        api_key = api_key or os.environ.get("SEARCHSTEWARD_API_KEY")
        if not api_key:
            raise ConfigError("SEARCHSTEWARD_API_KEY is required.")
        base_url = _require_https(base_url or os.environ.get("SEARCHSTEWARD_API_BASE") or DEFAULT_BASE, internal=internal)
        self._http = httpx.Client(
            base_url=base_url,
            headers={**(extra_headers or {}), "Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SearchStewardClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- low-level ----------------------------------------------------------

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        resp = self._http.request(method, path, **kw)
        if resp.is_success:
            return resp.json() if resp.content else {}
        detail = _extract_detail(resp)
        raise ApiError(resp.status_code, detail)

    # --- endpoints (one per PAT-allowlisted route) --------------------------

    def get_jobs(self, params: Dict[str, Any]) -> Any:
        return self._request("GET", "/api/v1/jobs", params={k: v for k, v in params.items() if v is not None})

    def get_review_candidates(self, params: Dict[str, Any]) -> Any:
        """Corpus-scoped review candidates — includes jobs never scored for this user.

        Deliberately NOT /api/v1/jobs: that endpoint is scoped to the materialised feed,
        so it can only return roles the ranker already surfaced. Reviewing through it
        grades the ranker on its own answers and can never find the misses.
        """
        return self._request(
            "GET", "/api/v1/match-review/candidates",
            params={k: v for k, v in params.items() if v is not None},
        )

    def submit_match_verdict(self, job_id: int, verdict: str, note: Optional[str] = None) -> Any:
        body: Dict[str, Any] = {"verdict": verdict}
        if note:
            body["note"] = note
        return self._request("POST", f"/api/v1/match-review/{job_id}/verdict", json=body)

    def get_review_summary(self) -> Any:
        return self._request("GET", "/api/v1/match-review/summary")

    def get_job_context(self, job_id: int) -> Any:
        return self._request("GET", f"/api/v1/jobs/{job_id}/context")

    def get_applications(self, params: Dict[str, Any]) -> Any:
        return self._request("GET", "/api/v1/applications", params={k: v for k, v in params.items() if v is not None})

    def get_application_kit(self, job_id: int, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", f"/api/v1/jobs/{job_id}/application-kit", params={k: v for k, v in (params or {}).items() if v is not None})

    def apply_track(self, job_id: int, note: Optional[str] = None) -> Any:
        body = {"note": note} if note else {}
        out = self._request("POST", f"/api/v1/applications/{job_id}/apply-track", json=body)
        # The API's legacy path (id is already an applications.id) answers {"status": "success"} with no
        # application_id; the id it updated is the one we sent, so the documented field is still true.
        if isinstance(out, dict) and out.get("status") == "success" and "application_id" not in out:
            out["application_id"] = job_id
        return out

    def patch_application(self, application_id: int, body: Dict[str, Any]) -> Any:
        return self._request("PATCH", f"/api/v1/applications/{application_id}", json=body)

    def add_note(self, application_id: int, note: str) -> Any:
        return self._request("POST", f"/api/v1/applications/{application_id}/notes", json={"note": note})

    def start_negotiation_playbook(self, application_id: int) -> Any:
        return self._request("POST", f"/api/v1/applications/{application_id}/negotiate-playbook", json={})

    def get_llm_job(self, job_id: str) -> Any:
        return self._request("GET", f"/api/v1/llm-jobs/{job_id}")

    def poll_llm_job(self, job_id: str, *, timeout: float = 90.0, interval: float = 2.0, sleep=time.sleep) -> Any:
        """Poll an async LLM job to completion. Returns the job dict.

        Raises ApiError on a failed job or TimeoutError if it doesn't finish in time.
        """
        deadline = _monotonic() + timeout
        while True:
            job = self.get_llm_job(job_id)
            status = (job or {}).get("status")
            if status in {"completed", "success", "done"}:
                return job
            if status in {"failed", "error"}:
                raise ApiError(500, (job or {}).get("error") or "LLM job failed")
            if _monotonic() >= deadline:
                raise TimeoutError(f"LLM job {job_id} did not complete within {timeout}s")
            sleep(interval)

    def get_resume(self) -> Any:
        return self._request("GET", "/api/v1/resume/profile")

    def create_resume_profile(self, name: str, text: str) -> Any:
        """POST a new résumé. The API makes a user's FIRST profile the primary one; the response is {"status", "id"} (no text)."""
        return self._request("POST", "/api/v1/resume/profiles", json={"name": name, "text": text})

    def update_resume_profile(self, profile_id: int, text: str, name: Optional[str] = None) -> Any:
        """PATCH one résumé by id (the route 400s without it). Sends only what changes: id, text and, when given, name."""
        body: Dict[str, Any] = {"id": profile_id, "text": text}
        if name:
            body["name"] = name
        return self._request("PATCH", "/api/v1/resume/profile", json=body)

    def get_offer(self, application_id: int) -> Any:
        return self._request("GET", f"/api/v1/applications/{application_id}/offer-workspace")

    def get_application(self, application_id: int) -> Any:
        return self._request("GET", f"/api/v1/applications/{application_id}")

    def save_match(self, job_id: int, note: Optional[str] = None) -> Any:
        body = {"note": note} if note else {}
        return self._request("POST", f"/api/v1/applications/{job_id}/save-watch", json=body)

    def get_user_settings(self) -> Any:
        """Merged user settings (DB + YAML defaults), secrets masked server-side."""
        return self._request("GET", "/api/v1/user/settings")

    def get_activation_status(self) -> Any:
        """Feed status + the cached thin-feed diagnosis (the matches-page panel's source)."""
        return self._request("GET", "/api/v1/activation/status")

    def preferences_from_search(self, body: Dict[str, Any]) -> Any:
        """Save a first search as the user's criteria. 409 when criteria already exist
        (never overwrites); an identical retry of the payload that set them is a 200 no-op."""
        return self._request("POST", "/api/v1/user/preferences/from-search", json=body)

    def patch_user_settings(self, category: str, updates: Dict[str, Any]) -> Any:
        """Partial update of ONE settings category.

        The route validates scoring categories against typed leaf models, so an
        unknown field is a 422 rather than a silent write, and it triggers the
        criteria-version bump + hash invalidation + feed refresh itself.
        """
        return self._request(
            "PATCH", "/api/v1/user/settings", json={"category": category, "updates": updates}
        )

    def dismiss_match(self, job_id: int, reason_code: str, note: Optional[str] = None) -> Any:
        body = {"reason_code": reason_code, "note": note}
        return self._request("POST", f"/api/v1/jobs/{job_id}/feedback", json=body)

    def restore_match(self, job_id: int) -> Any:
        return self._request("POST", f"/api/v1/jobs/{job_id}/restore", json={})

    def list_questions(self, application_id: Optional[int] = None) -> Any:
        return self._request("GET", "/api/v1/questions", params={"application_id": application_id} if application_id else {})

    def save_question(
        self, question: str, answer: Optional[str] = None, application_id: Optional[int] = None, category: Optional[str] = None
    ) -> Any:
        body = {"question": question}
        if answer is not None:
            body["answer"] = answer
        if application_id is not None:
            body["application_id"] = application_id
        if category is not None:
            body["category"] = category
        return self._request("POST", "/api/v1/questions", json=body)

    def track_external_application(
        self,
        company: str,
        title: str,
        url: Optional[str] = None,
        location: Optional[str] = None,
        status: Optional[str] = None,
        applied_date: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Any:
        # Wire names are the applications table's columns: the API stores the body as-is and drops
        # unknown keys, so "title"/"url"/"note"/"applied_date" 400'd ("role is required") or vanished.
        # The tool records an application you SUBMITTED; without a status the API stores 'considering'.
        body: Dict[str, Any] = {"company": company, "role": title, "status": status or "applied"}
        for key, value in (("job_url", url), ("location", location),
                           ("date_applied", applied_date), ("notes", note)):
            if value is not None:
                body[key] = value
        out = self._request("POST", "/api/v1/applications", json=body)
        if isinstance(out, dict) and "application_id" not in out and "id" in out:
            out["application_id"] = out["id"]  # the API answers {"status": "success", "id": N}
        return out


def _extract_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return resp.text[:300] or resp.reason_phrase
    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    if isinstance(detail, dict):
        # Entitlement-denied bodies carry a human message.
        return str(detail.get("message") or detail.get("upgrade_reason") or detail)
    return str(detail)


def _monotonic() -> float:
    # Wrapped so tests can hold time still without patching the stdlib globally.
    return time.monotonic()
