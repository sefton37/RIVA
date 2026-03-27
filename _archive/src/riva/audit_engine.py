"""Audit Engine: post-completion verification against contract criteria.

Evaluates each criterion in a contract after agent completion.
Uses subprocess git commands in the agent workspace — no git library.

Criterion evaluators:
    file_exists — Path(agent_cwd / path).exists()
    function_defined — grep -n "def name" in file
    git_contains_change — git diff --name-only includes path
    git_commit_message — git log --oneline contains keyword
    manual_verification — always 'inconclusive', flagged for user

Non-zero git return codes → 'inconclusive' (not 'failed').
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from riva.contract_store import get_contract
from riva.db import get_connection, transaction
from riva.errors import AuditError
from riva.models import VerificationCriterion

logger = logging.getLogger(__name__)


def _evaluate_file_exists(cwd: Path, criterion: VerificationCriterion) -> dict[str, Any]:
    """Check if a file exists in the workspace."""
    path = criterion.path
    if not path:
        return {"status": "inconclusive", "evidence": "No path specified"}

    target = cwd / path
    exists = target.exists()
    return {
        "status": "passed" if exists else "failed",
        "evidence": f"{path} {'exists' if exists else 'not found'}",
    }


def _evaluate_function_defined(
    cwd: Path, criterion: VerificationCriterion
) -> dict[str, Any]:
    """Check if a function is defined in a file via grep.

    Also checks implementation depth — flags stub-only functions
    (body is just 'pass', '...', or empty) as 'passed_stub' evidence
    so callers can distinguish real implementations from placeholders.
    """
    file_path = criterion.file
    func_name = criterion.name
    if not file_path or not func_name:
        return {"status": "inconclusive", "evidence": "Missing file or function name"}

    target = cwd / file_path
    if not target.exists():
        return {"status": "failed", "evidence": f"{file_path} does not exist"}

    try:
        result = subprocess.run(
            ["grep", "-n", f"def {func_name}", str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            line = result.stdout.strip().split("\n")[0]
            line_num = int(line.split(":")[0]) if ":" in line else 0

            # Check if implementation is a stub
            is_stub = _check_function_is_stub(target, func_name, line_num)
            if is_stub:
                return {
                    "status": "passed",
                    "evidence": f"Found: {line} (WARNING: stub implementation — body is pass/empty)",
                    "stub": True,
                }
            return {"status": "passed", "evidence": f"Found: {line}"}
        return {
            "status": "failed",
            "evidence": f"def {func_name} not found in {file_path}",
        }
    except subprocess.TimeoutExpired:
        return {"status": "inconclusive", "evidence": "grep timed out"}


def _check_function_is_stub(file_path: Path, func_name: str, def_line: int) -> bool:
    """Check if a function body is just pass/... (a stub).

    Reads lines after the def statement. If the only non-empty,
    non-comment, non-docstring line is 'pass' or '...', it's a stub.
    """
    try:
        lines = file_path.read_text().splitlines()
        if def_line <= 0 or def_line > len(lines):
            return False

        # Get indentation of def line
        def_text = lines[def_line - 1]
        def_indent = len(def_text) - len(def_text.lstrip())

        body_lines: list[str] = []
        in_docstring = False

        for i in range(def_line, len(lines)):
            line = lines[i]
            stripped = line.strip()

            # Empty line
            if not stripped:
                continue

            # Detect end of function (dedent)
            line_indent = len(line) - len(line.lstrip())
            if line_indent <= def_indent and stripped and not stripped.startswith("#"):
                break

            # Skip docstrings
            if '"""' in stripped or "'''" in stripped:
                if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                    in_docstring = not in_docstring
                continue
            if in_docstring:
                continue

            # Skip comments
            if stripped.startswith("#"):
                continue

            body_lines.append(stripped)

        # Stub if body is empty, just 'pass', just '...', or just 'return None'
        if not body_lines:
            return True
        if all(b in ("pass", "...", "return None", "return") for b in body_lines):
            return True

        return False
    except Exception:
        return False


def _evaluate_workspace_clean(
    cwd: Path, criterion: VerificationCriterion
) -> dict[str, Any]:
    """Check workspace for unexpected files.

    Compares workspace contents against a list of expected paths.
    Flags any unexpected files (especially suspicious ones like
    .env, credentials, debug scripts).
    """
    expected_paths = set()
    desc = criterion.description or ""
    # Parse expected paths from description (comma-separated)
    for part in desc.split(","):
        part = part.strip()
        if part:
            expected_paths.add(part)

    # Walk workspace (skip .git)
    unexpected: list[str] = []
    suspicious: list[str] = []

    _SUSPICIOUS_PATTERNS = {
        ".env", ".secret", "credentials", "debug_", ".backup",
        "password", "token", ".key", ".pem",
    }

    for item in cwd.rglob("*"):
        if ".git" in item.parts:
            continue
        if not item.is_file():
            continue

        rel = str(item.relative_to(cwd))
        if rel == "CLAUDE.md" or rel == "README.md" or rel == ".gitignore":
            continue  # Standard workspace files

        if rel not in expected_paths:
            unexpected.append(rel)
            if any(pat in rel.lower() for pat in _SUSPICIOUS_PATTERNS):
                suspicious.append(rel)

    if suspicious:
        return {
            "status": "failed",
            "evidence": f"Suspicious files in workspace: {', '.join(suspicious)}",
            "unexpected_files": unexpected,
        }
    if unexpected:
        return {
            "status": "passed",
            "evidence": f"Workspace has {len(unexpected)} extra file(s): {', '.join(unexpected[:5])}",
            "unexpected_files": unexpected,
        }
    return {
        "status": "passed",
        "evidence": "Workspace clean — no unexpected files",
    }


def _evaluate_git_contains_change(
    cwd: Path, criterion: VerificationCriterion, since: str | None = None
) -> dict[str, Any]:
    """Check if a path appears in git diff."""
    path = criterion.path
    if not path:
        return {"status": "inconclusive", "evidence": "No path specified"}

    # Get commit count since session start
    diff_args = ["git", "-C", str(cwd), "diff", "--name-only"]

    if since:
        # Count commits since timestamp
        try:
            log_result = subprocess.run(
                ["git", "-C", str(cwd), "log", f"--since={since}", "--oneline"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if log_result.returncode == 0:
                n = len(log_result.stdout.strip().split("\n"))
                if n > 0:
                    diff_args.append(f"HEAD~{n}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    else:
        diff_args.append("HEAD~5")  # Default: last 5 commits

    try:
        result = subprocess.run(
            diff_args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            err = result.stderr.strip()[:100]
            return {"status": "inconclusive", "evidence": f"git diff failed: {err}"}

        changed = result.stdout.strip().split("\n")
        matches = [f for f in changed if path.rstrip("/") in f]
        if matches:
            return {
                "status": "passed",
                "evidence": f"Changed files matching '{path}': {', '.join(matches[:5])}",
            }
        return {
            "status": "failed",
            "evidence": f"No changes found matching '{path}'",
        }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"status": "inconclusive", "evidence": "git not available or timed out"}


def _evaluate_git_commit_message(
    cwd: Path, criterion: VerificationCriterion
) -> dict[str, Any]:
    """Check if a keyword appears in recent git log."""
    keyword = criterion.keyword
    if not keyword:
        return {"status": "inconclusive", "evidence": "No keyword specified"}

    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "log", "--oneline", "-20"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"status": "inconclusive", "evidence": "git log failed"}

        lines = result.stdout.strip().split("\n")
        matches = [line for line in lines if keyword.lower() in line.lower()]
        if matches:
            return {
                "status": "passed",
                "evidence": f"Found in commit: {matches[0]}",
            }
        return {
            "status": "failed",
            "evidence": f"'{keyword}' not found in last 20 commits",
        }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"status": "inconclusive", "evidence": "git not available or timed out"}


def _evaluate_manual(criterion: VerificationCriterion) -> dict[str, Any]:
    """Manual verification — always inconclusive."""
    desc = criterion.description or "Manual check required"
    return {"status": "inconclusive", "evidence": desc}


_EVALUATORS = {
    "file_exists": _evaluate_file_exists,
    "function_defined": _evaluate_function_defined,
    "git_contains_change": _evaluate_git_contains_change,
    "git_commit_message": _evaluate_git_commit_message,
    "workspace_clean": _evaluate_workspace_clean,
    "manual_verification": lambda cwd, c: _evaluate_manual(c),
}


def _get_git_summary(cwd: Path) -> tuple[str, list[str]]:
    """Get git diff summary and changed files list."""
    diff_summary = ""
    files_changed: list[str] = []

    try:
        stat_result = subprocess.run(
            ["git", "-C", str(cwd), "diff", "--stat", "HEAD~5"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if stat_result.returncode == 0:
            diff_summary = stat_result.stdout.strip()

        names_result = subprocess.run(
            ["git", "-C", str(cwd), "diff", "--name-only", "HEAD~5"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if names_result.returncode == 0:
            files_changed = [f for f in names_result.stdout.strip().split("\n") if f]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return diff_summary, files_changed


def run_audit(
    contract_id: str,
    agent_cwd: str,
    *,
    triggered_by: str = "auto",
    session_started_at: str | None = None,
) -> dict[str, Any]:
    """Run an audit against a contract's criteria.

    Args:
        contract_id: The contract to audit.
        agent_cwd: The agent's workspace directory.
        triggered_by: 'auto' (stream broker done event) or 'user'.
        session_started_at: ISO timestamp for git since filtering.

    Returns:
        Audit result dict with per-criterion results and overall verdict.
    """
    contract = get_contract(contract_id)
    if contract is None:
        raise AuditError(f"Contract not found: {contract_id}")

    cwd = Path(agent_cwd)
    criteria_results: list[dict[str, Any]] = []

    for criterion in contract.verification_criteria:
        evaluator = _EVALUATORS.get(criterion.type)
        if evaluator is None:
            result = {"status": "inconclusive", "evidence": f"Unknown type: {criterion.type}"}
        elif criterion.type == "git_contains_change":
            result = _evaluate_git_contains_change(cwd, criterion, since=session_started_at)
        else:
            result = evaluator(cwd, criterion)

        criteria_results.append({
            "criterion": criterion.to_dict(),
            **result,
        })

    # Compute overall verdict
    statuses = [r["status"] for r in criteria_results]
    if not statuses:
        overall = "inconclusive"
    elif all(s == "passed" for s in statuses):
        overall = "passed"
    elif all(s == "failed" for s in statuses):
        overall = "failed"
    elif all(s == "inconclusive" for s in statuses):
        overall = "inconclusive"
    elif any(s == "failed" for s in statuses):
        overall = "partial"
    else:
        overall = "partial"

    # Git summary
    diff_summary, files_changed = _get_git_summary(cwd)

    # Verdict explanation
    passed = sum(1 for s in statuses if s == "passed")
    failed = sum(1 for s in statuses if s == "failed")
    inconclusive = sum(1 for s in statuses if s == "inconclusive")
    total = len(statuses)
    explanation = (
        f"{passed} passed, {failed} failed, {inconclusive} inconclusive"
        f" out of {total} criteria"
    )

    # Persist audit
    audit_id = f"audit-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    with transaction() as conn:
        conn.execute(
            "INSERT INTO riva_audits "
            "(id, contract_id, agent_id, triggered_by, git_diff_summary, "
            "files_changed_json, criteria_results_json, overall_verdict, "
            "verdict_explanation, audited_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id,
                contract_id,
                contract.agent_id,
                triggered_by,
                diff_summary,
                json.dumps(files_changed),
                json.dumps(criteria_results),
                overall,
                explanation,
                now,
                now,
            ),
        )

        # Update contract status based on verdict
        if overall == "passed":
            conn.execute(
                "UPDATE riva_contracts SET status='fulfilled', updated_at=? WHERE id=?",
                (now, contract_id),
            )
        elif overall == "failed":
            conn.execute(
                "UPDATE riva_contracts SET status='violated', updated_at=? WHERE id=?",
                (now, contract_id),
            )

    logger.info(
        "Audit %s for contract %s: %s (%s)",
        audit_id, contract_id, overall, explanation,
    )

    return {
        "audit_id": audit_id,
        "contract_id": contract_id,
        "agent_id": contract.agent_id,
        "triggered_by": triggered_by,
        "overall_verdict": overall,
        "verdict_explanation": explanation,
        "criteria_results": criteria_results,
        "git_diff_summary": diff_summary,
        "files_changed": files_changed,
        "audited_at": now,
    }


def get_audit(audit_id: str) -> dict[str, Any] | None:
    """Retrieve an audit from the database."""
    conn = get_connection(readonly=True)
    try:
        row = conn.execute("SELECT * FROM riva_audits WHERE id=?", (audit_id,)).fetchone()
        if row is None:
            return None
        return {
            "audit_id": row["id"],
            "contract_id": row["contract_id"],
            "agent_id": row["agent_id"],
            "triggered_by": row["triggered_by"],
            "overall_verdict": row["overall_verdict"],
            "verdict_explanation": row["verdict_explanation"],
            "criteria_results": json.loads(row["criteria_results_json"] or "[]"),
            "git_diff_summary": row["git_diff_summary"],
            "files_changed": json.loads(row["files_changed_json"] or "[]"),
            "audited_at": row["audited_at"],
        }
    finally:
        conn.close()


def list_audits(contract_id: str | None = None) -> list[dict[str, Any]]:
    """List audits, optionally filtered by contract."""
    conn = get_connection(readonly=True)
    try:
        if contract_id:
            rows = conn.execute(
                "SELECT id FROM riva_audits WHERE contract_id=? ORDER BY created_at DESC",
                (contract_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM riva_audits ORDER BY created_at DESC"
            ).fetchall()

        return [
            audit
            for row in rows
            if (audit := get_audit(row["id"])) is not None
        ]
    finally:
        conn.close()
