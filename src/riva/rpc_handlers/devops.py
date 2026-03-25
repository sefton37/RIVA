"""RPC handlers for DevOps integration (Forgejo + Woodpecker CI).

Thin wrappers around the Forgejo and Woodpecker clients.
Clients are initialized at registration time with env-var config.
Methods gracefully degrade if clients are not configured.

Methods:
    riva/devops/repos/list        — List Forgejo repos
    riva/devops/repos/get         — Get repo details
    riva/devops/commits/recent    — Recent commits for a repo
    riva/devops/branches/list     — List branches
    riva/devops/pr/list           — List pull requests
    riva/devops/pr/create         — Create a pull request
    riva/devops/pr/merge          — Merge a pull request
    riva/devops/ci/repos          — List Woodpecker repos
    riva/devops/ci/status         — Latest pipeline status for a repo
    riva/devops/ci/pipelines      — List pipelines for a repo
    riva/devops/ci/trigger        — Trigger a new build
    riva/devops/ci/logs           — Get pipeline logs
    riva/devops/status            — Overall DevOps connectivity status
"""

from __future__ import annotations

import logging
from typing import Any

from riva.errors import RivaError

logger = logging.getLogger(__name__)

# Module-level clients, set during registration
_forgejo = None
_woodpecker = None


def set_clients(forgejo, woodpecker) -> None:
    global _forgejo, _woodpecker
    _forgejo = forgejo
    _woodpecker = woodpecker


def _require_forgejo():
    if _forgejo is None or not _forgejo.configured:
        raise RivaError("Forgejo not configured (set FORGEJO_URL and FORGEJO_TOKEN)")
    return _forgejo


def _require_woodpecker():
    if _woodpecker is None or not _woodpecker.configured:
        raise RivaError("Woodpecker not configured (set WOODPECKER_URL and WOODPECKER_TOKEN)")
    return _woodpecker


# ── DevOps Status ────────────────────────────────────────────────────


def handle_devops_status(**_kw) -> dict[str, Any]:
    """Report connectivity status for both services."""
    result: dict[str, Any] = {
        "forgejo": {"configured": False, "connected": False},
        "woodpecker": {"configured": False, "connected": False},
    }

    if _forgejo and _forgejo.configured:
        result["forgejo"]["configured"] = True
        try:
            _forgejo.list_repos(limit=1)
            result["forgejo"]["connected"] = True
        except Exception as exc:
            result["forgejo"]["error"] = str(exc)

    if _woodpecker and _woodpecker.configured:
        result["woodpecker"]["configured"] = True
        try:
            _woodpecker.get_current_user()
            result["woodpecker"]["connected"] = True
        except Exception as exc:
            result["woodpecker"]["error"] = str(exc)

    return result


# ── Forgejo: Repos ───────────────────────────────────────────────────


def handle_repos_list(**_kw) -> dict[str, Any]:
    fg = _require_forgejo()
    repos = fg.list_repos()
    return {
        "repos": [
            {
                "name": r.get("name"),
                "full_name": r.get("full_name"),
                "description": r.get("description", ""),
                "default_branch": r.get("default_branch", "main"),
                "updated_at": r.get("updated_at"),
                "html_url": r.get("html_url"),
                "stars": r.get("stars_count", 0),
                "open_issues": r.get("open_issues_count", 0),
            }
            for r in repos
        ]
    }


def handle_repos_get(*, repo: str = "", **_kw) -> dict[str, Any]:
    if not repo:
        raise RivaError("repo is required")
    fg = _require_forgejo()
    return fg.get_repo(repo)


# ── Forgejo: Commits ─────────────────────────────────────────────────


def handle_commits_recent(
    *, repo: str = "", branch: str | None = None, limit: int = 10, **_kw
) -> dict[str, Any]:
    if not repo:
        raise RivaError("repo is required")
    fg = _require_forgejo()
    commits = fg.list_commits(repo, branch=branch, limit=limit)
    return {
        "commits": [
            {
                "sha": c.get("sha", "")[:12],
                "message": c.get("commit", {}).get("message", "").split("\n")[0],
                "author": c.get("commit", {}).get("author", {}).get("name", ""),
                "date": c.get("commit", {}).get("author", {}).get("date", ""),
            }
            for c in commits
        ]
    }


# ── Forgejo: Branches ────────────────────────────────────────────────


def handle_branches_list(*, repo: str = "", **_kw) -> dict[str, Any]:
    if not repo:
        raise RivaError("repo is required")
    fg = _require_forgejo()
    branches = fg.list_branches(repo)
    return {
        "branches": [
            {"name": b.get("name"), "protected": b.get("protected", False)}
            for b in branches
        ]
    }


# ── Forgejo: Pull Requests ───────────────────────────────────────────


def handle_pr_list(
    *, repo: str = "", state: str = "open", **_kw
) -> dict[str, Any]:
    if not repo:
        raise RivaError("repo is required")
    fg = _require_forgejo()
    pulls = fg.list_pulls(repo, state=state)
    return {
        "pulls": [
            {
                "number": p.get("number"),
                "title": p.get("title"),
                "state": p.get("state"),
                "user": p.get("user", {}).get("login", ""),
                "head": p.get("head", {}).get("ref", ""),
                "base": p.get("base", {}).get("ref", ""),
                "created_at": p.get("created_at"),
                "html_url": p.get("html_url"),
            }
            for p in pulls
        ]
    }


def handle_pr_create(
    *, repo: str = "", title: str = "", head: str = "",
    base: str = "main", body: str = "", **_kw
) -> dict[str, Any]:
    if not repo or not title or not head:
        raise RivaError("repo, title, and head branch are required")
    fg = _require_forgejo()
    return fg.create_pull(repo, title=title, head=head, base=base, body=body)


def handle_pr_merge(*, repo: str = "", pr_number: int = 0, **_kw) -> dict[str, Any]:
    if not repo or not pr_number:
        raise RivaError("repo and pr_number are required")
    fg = _require_forgejo()
    return fg.merge_pull(repo, pr_number)


# ── Woodpecker: Repos ────────────────────────────────────────────────


def handle_ci_repos(**_kw) -> dict[str, Any]:
    wp = _require_woodpecker()
    repos = wp.list_repos()
    return {
        "repos": [
            {
                "id": r.get("id"),
                "full_name": r.get("full_name"),
                "active": r.get("active", False),
            }
            for r in repos
        ]
    }


# ── Woodpecker: Pipelines ───────────────────────────────────────────


def handle_ci_status(*, repo_id: int = 0, **_kw) -> dict[str, Any]:
    """Get latest pipeline status for a repo."""
    if not repo_id:
        raise RivaError("repo_id is required")
    wp = _require_woodpecker()
    pipelines = wp.list_pipelines(repo_id, per_page=1)
    if not pipelines:
        return {"latest": None}
    p = pipelines[0]
    return {
        "latest": {
            "number": p.get("number"),
            "status": p.get("status"),
            "event": p.get("event"),
            "branch": p.get("branch"),
            "message": p.get("message", "").split("\n")[0],
            "started_at": p.get("started_at"),
            "finished_at": p.get("finished_at"),
        }
    }


def handle_ci_pipelines(
    *, repo_id: int = 0, page: int = 1, per_page: int = 10, **_kw
) -> dict[str, Any]:
    if not repo_id:
        raise RivaError("repo_id is required")
    wp = _require_woodpecker()
    pipelines = wp.list_pipelines(repo_id, page=page, per_page=per_page)
    return {
        "pipelines": [
            {
                "number": p.get("number"),
                "status": p.get("status"),
                "event": p.get("event"),
                "branch": p.get("branch"),
                "message": p.get("message", "").split("\n")[0],
                "started_at": p.get("started_at"),
                "finished_at": p.get("finished_at"),
            }
            for p in pipelines
        ]
    }


def handle_ci_trigger(
    *, repo_id: int = 0, branch: str = "main", **_kw
) -> dict[str, Any]:
    if not repo_id:
        raise RivaError("repo_id is required")
    wp = _require_woodpecker()
    result = wp.trigger_pipeline(repo_id, branch=branch)
    return {
        "number": result.get("number"),
        "status": result.get("status"),
        "branch": branch,
    }


def handle_ci_logs(*, repo_id: int = 0, number: int = 0, **_kw) -> dict[str, Any]:
    if not repo_id or not number:
        raise RivaError("repo_id and pipeline number are required")
    wp = _require_woodpecker()
    logs = wp.get_pipeline_logs(repo_id, number)
    return {"logs": logs}
