"""Forgejo REST API client.

Talks to the Forgejo (Gitea-compatible) instance for git operations:
repos, commits, branches, pull requests.

Configuration via environment variables:
    FORGEJO_URL    — Base API URL
    FORGEJO_TOKEN  — API access credential
    FORGEJO_ORG    — Organization name (default: talking-rock)
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_ORG = "talking-rock"
_TIMEOUT = 15.0


class ForgejoClient:
    """Synchronous Forgejo API client."""

    def __init__(
        self,
        base_url: str | None = None,
        auth_token: str | None = None,
        org: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("FORGEJO_URL", "")).rstrip("/")
        self._auth = auth_token or os.environ.get("FORGEJO_TOKEN", "")
        self.org = org or os.environ.get("FORGEJO_ORG", _DEFAULT_ORG)
        self._api = f"{self.base_url}/api/v1"

    @property
    def configured(self) -> bool:
        return bool(self._auth) and bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        if self._auth:
            h["Authorization"] = f"token {self._auth}"
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

    def list_repos(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """List repos in the organization."""
        return self._get(f"/orgs/{quote(self.org)}/repos", {"limit": limit})

    def get_repo(self, repo: str) -> dict[str, Any]:
        """Get repo details."""
        return self._get(f"/repos/{quote(self.org)}/{quote(repo)}")

    # ── Commits ──

    def list_commits(
        self, repo: str, *, branch: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """List recent commits."""
        params: dict[str, Any] = {"limit": limit}
        if branch:
            params["sha"] = branch
        return self._get(f"/repos/{quote(self.org)}/{quote(repo)}/commits", params)

    # ── Branches ──

    def list_branches(self, repo: str) -> list[dict[str, Any]]:
        """List branches."""
        return self._get(f"/repos/{quote(self.org)}/{quote(repo)}/branches")

    # ── Pull Requests ──

    def list_pulls(
        self, repo: str, *, state: str = "open", limit: int = 20
    ) -> list[dict[str, Any]]:
        """List pull requests."""
        return self._get(
            f"/repos/{quote(self.org)}/{quote(repo)}/pulls",
            {"state": state, "limit": limit},
        )

    def create_pull(
        self,
        repo: str,
        *,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
    ) -> dict[str, Any]:
        """Create a pull request."""
        return self._post(
            f"/repos/{quote(self.org)}/{quote(repo)}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
        )

    def merge_pull(self, repo: str, pr_number: int) -> dict[str, Any]:
        """Merge a pull request."""
        url = f"{self._api}/repos/{quote(self.org)}/{quote(repo)}/pulls/{pr_number}/merge"
        resp = httpx.post(
            url,
            headers=self._headers(),
            json={"Do": "merge"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Issues (Forgejo issues, not PM issues) ──

    def list_issues(
        self, repo: str, *, state: str = "open", limit: int = 20
    ) -> list[dict[str, Any]]:
        """List Forgejo issues."""
        return self._get(
            f"/repos/{quote(self.org)}/{quote(repo)}/issues",
            {"state": state, "limit": limit, "type": "issues"},
        )
