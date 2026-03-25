"""Automation loops: the glue between audit, DevOps, and PM.

Chains that fire after events:

    on_audit_passed(contract_id, audit_id, agent_cwd)
        1. Find linked PM issue via contract.riva_contract_id
        2. Create PR on Forgejo (if repo is linked)
        3. Start CI polling — when green, close the PM issue
        4. Propose scene completion

    on_ci_green(issue_id)
        1. Update PM issue status to Done
        2. Log to research table

Each step is best-effort: failure at any step logs a warning
but doesn't block subsequent steps. The chain is not transactional.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Module-level clients, set during registration
_forgejo = None
_woodpecker = None
_ci_poll_threads: dict[str, threading.Thread] = {}

# Retry budget: max CI polls before giving up
_CI_POLL_MAX = 60  # 60 polls * 10s = 10 minutes
_CI_POLL_INTERVAL = 10.0


def set_devops_clients(forgejo, woodpecker) -> None:
    global _forgejo, _woodpecker
    _forgejo = forgejo
    _woodpecker = woodpecker


def on_audit_passed(
    contract_id: str,
    audit_id: str,
    agent_cwd: str,
) -> dict[str, Any]:
    """Full automation chain after a passing audit.

    Returns a summary dict of what was attempted and what succeeded.
    """
    summary: dict[str, Any] = {
        "contract_id": contract_id,
        "audit_id": audit_id,
        "steps": {},
    }

    # Step 1: Find linked PM issue
    issue_id = _find_linked_issue(contract_id)
    summary["steps"]["find_issue"] = {"issue_id": issue_id}

    # Step 2: Create PR (if Forgejo is configured and repo is determinable)
    pr_number = None
    repo_name = _guess_repo_from_cwd(agent_cwd)
    if repo_name and _forgejo and _forgejo.configured:
        pr_number = _try_create_pr(repo_name, contract_id, audit_id)
        summary["steps"]["create_pr"] = {
            "repo": repo_name,
            "pr_number": pr_number,
        }
    else:
        summary["steps"]["create_pr"] = {"skipped": True, "reason": "no repo or Forgejo not configured"}

    # Step 3: Start CI polling in background (if Woodpecker is configured)
    if pr_number and _woodpecker and _woodpecker.configured:
        _start_ci_poll(repo_name, issue_id, contract_id)
        summary["steps"]["ci_poll"] = {"started": True, "repo": repo_name}
    elif issue_id:
        # No CI — just close the issue directly
        _close_issue(issue_id, contract_id)
        summary["steps"]["close_issue"] = {"issue_id": issue_id, "direct": True}
    else:
        summary["steps"]["ci_poll"] = {"skipped": True}

    # Step 4: Scene proposal (already handled by the caller in service.py)
    summary["steps"]["scene_proposal"] = {"handled_by": "service.py"}

    logger.info("Automation chain for contract %s: %s", contract_id, summary["steps"])
    return summary


def _find_linked_issue(contract_id: str) -> str | None:
    """Find a PM issue linked to this contract via riva_contract_id."""
    try:
        from riva.pm_store import list_issues

        # Search all issues for one linked to this contract
        all_issues = list_issues()
        for issue in all_issues:
            if issue.riva_contract_id == contract_id:
                return issue.id
    except Exception:
        logger.debug("Could not search for linked issue for contract %s", contract_id)
    return None


def _guess_repo_from_cwd(agent_cwd: str) -> str | None:
    """Guess the Forgejo repo name from the agent's working directory.

    Convention: agent workspaces are at ~/dev/{RepoName}/ or
    ~/dev/talkingrock/agents/{slug}/.
    """
    from pathlib import Path

    cwd = Path(agent_cwd)

    # Map common directory names to Forgejo repo names
    known_repos = {
        "cairn": "cairn",
        "reos": "ReOS",
        "riva": "RIVA",
        "sieve": "Sieve",
        "lithium": "Lithium",
        "talkingrock-core": "talkingrock-core",
    }

    # Walk up the path looking for a known repo name
    for part in reversed(cwd.parts):
        part_lower = part.lower()
        for dirname, repo in known_repos.items():
            if dirname in part_lower:
                return repo

    return None


def _try_create_pr(
    repo_name: str, contract_id: str, audit_id: str
) -> int | None:
    """Attempt to create a PR on Forgejo. Returns PR number or None."""
    try:
        # Get the current branch from git
        import subprocess

        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        branch = result.stdout.strip()
        if not branch or branch == "main":
            logger.debug("Cannot create PR: on main branch or no branch detected")
            return None

        pr = _forgejo.create_pull(
            repo_name,
            title=f"RIVA: contract {contract_id[:16]} fulfilled",
            head=branch,
            base="main",
            body=f"Auto-generated by RIVA after audit {audit_id} passed.\n\n"
                 f"Contract: `{contract_id}`\nAudit: `{audit_id}`",
        )
        pr_number = pr.get("number")
        logger.info("Created PR #%s on %s for contract %s", pr_number, repo_name, contract_id)
        return pr_number
    except Exception as exc:
        logger.warning("Failed to create PR on %s: %s", repo_name, exc)
        return None


def _start_ci_poll(
    repo_name: str | None,
    issue_id: str | None,
    contract_id: str,
) -> None:
    """Start a background thread that polls Woodpecker for CI completion."""
    thread_key = f"ci-{contract_id}"
    if thread_key in _ci_poll_threads and _ci_poll_threads[thread_key].is_alive():
        return

    def _poll():
        try:
            # Find the Woodpecker repo ID
            repos = _woodpecker.list_repos()
            wp_repo = None
            for r in repos:
                if repo_name and repo_name.lower() in r.get("full_name", "").lower():
                    wp_repo = r
                    break

            if wp_repo is None:
                logger.debug("No Woodpecker repo found for %s", repo_name)
                return

            repo_id = wp_repo["id"]

            for _ in range(_CI_POLL_MAX):
                time.sleep(_CI_POLL_INTERVAL)
                try:
                    pipelines = _woodpecker.list_pipelines(repo_id, per_page=1)
                    if not pipelines:
                        continue

                    latest = pipelines[0]
                    status = latest.get("status", "")

                    if status == "success":
                        logger.info(
                            "CI green for %s (pipeline #%s) — closing issue",
                            repo_name, latest.get("number"),
                        )
                        if issue_id:
                            _close_issue(issue_id, contract_id)
                        return

                    if status in ("failure", "error", "killed"):
                        logger.warning(
                            "CI failed for %s (pipeline #%s, status=%s)",
                            repo_name, latest.get("number"), status,
                        )
                        return

                    # Still running or pending — continue polling
                except Exception as exc:
                    logger.debug("CI poll error: %s", exc)

            logger.warning("CI poll timed out for contract %s", contract_id)
        except Exception:
            logger.exception("CI poll thread error for %s", contract_id)

    thread = threading.Thread(target=_poll, name=thread_key, daemon=True)
    thread.start()
    _ci_poll_threads[thread_key] = thread
    logger.info("Started CI poll thread for contract %s", contract_id)


def _close_issue(issue_id: str, contract_id: str) -> None:
    """Close a PM issue after CI passes."""
    try:
        from riva.pm_store import update_issue

        update_issue(issue_id, status="Done")
        logger.info("PM issue %s closed (contract %s fulfilled, CI green)", issue_id, contract_id)
    except Exception as exc:
        logger.warning("Failed to close issue %s: %s", issue_id, exc)
