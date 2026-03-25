"""Woodpecker CI REST API client.

Talks to Woodpecker for CI/CD operations:
pipeline status, triggering builds, reading logs.

Configuration via environment variables:
    WOODPECKER_URL   — Base API URL
    WOODPECKER_TOKEN — API bearer credential
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


class WoodpeckerClient:
    """Synchronous Woodpecker CI API client."""

    def __init__(
        self,
        base_url: str | None = None,
        auth_bearer: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("WOODPECKER_URL", "")).rstrip("/")
        self._auth = auth_bearer or os.environ.get("WOODPECKER_TOKEN", "")
        self._api = f"{self.base_url}/api"

    @property
    def configured(self) -> bool:
        return bool(self._auth) and bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        if self._auth:
            h["Authorization"] = f"Bearer {self._auth}"
        return h

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._api}{path}"
        resp = httpx.get(url, headers=self._headers(), params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        url = f"{self._api}{path}"
        resp = httpx.post(url, headers=self._headers(), json=json_body, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ── Repos ──

    def list_repos(self) -> list[dict[str, Any]]:
        """List all repos registered in Woodpecker."""
        return self._get("/repos")

    def get_repo(self, repo_id: int) -> dict[str, Any]:
        """Get repo details by numeric ID."""
        return self._get(f"/repos/{repo_id}")

    # ── Pipelines ──

    def list_pipelines(
        self, repo_id: int, *, page: int = 1, per_page: int = 20
    ) -> list[dict[str, Any]]:
        """List pipelines (builds) for a repo."""
        return self._get(
            f"/repos/{repo_id}/pipelines",
            {"page": page, "perPage": per_page},
        )

    def get_pipeline(self, repo_id: int, number: int) -> dict[str, Any]:
        """Get a single pipeline by number."""
        return self._get(f"/repos/{repo_id}/pipelines/{number}")

    def trigger_pipeline(
        self, repo_id: int, *, branch: str = "main"
    ) -> dict[str, Any]:
        """Trigger a new pipeline build."""
        return self._post(
            f"/repos/{repo_id}/pipelines",
            {"branch": branch},
        )

    # ── Logs ──

    def get_pipeline_logs(
        self, repo_id: int, number: int
    ) -> list[dict[str, Any]]:
        """Get logs for a pipeline. Returns list of step log entries."""
        return self._get(f"/repos/{repo_id}/pipelines/{number}/logs")

    # ── User ──

    def get_current_user(self) -> dict[str, Any]:
        """Get the authenticated user."""
        return self._get("/user")
