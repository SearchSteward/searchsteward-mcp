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


def _require_https(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    is_local = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not is_local:
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
    ):
        api_key = api_key or os.environ.get("SEARCHSTEWARD_API_KEY")
        if not api_key:
            raise ConfigError("SEARCHSTEWARD_API_KEY is required.")
        base_url = _require_https(base_url or os.environ.get("SEARCHSTEWARD_API_BASE") or DEFAULT_BASE)
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
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

    def get_job_context(self, job_id: int) -> Any:
        return self._request("GET", f"/api/v1/jobs/{job_id}/context")

    def get_applications(self, params: Dict[str, Any]) -> Any:
        return self._request("GET", "/api/v1/applications", params={k: v for k, v in params.items() if v is not None})

    def apply_track(self, job_id: int, note: Optional[str] = None) -> Any:
        body = {"note": note} if note else {}
        return self._request("POST", f"/api/v1/applications/{job_id}/apply-track", json=body)

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
