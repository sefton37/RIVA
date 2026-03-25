"""Tests for the RIVA audit engine."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from riva.audit_engine import (
    _evaluate_file_exists,
    _evaluate_function_defined,
    _evaluate_git_commit_message,
    _evaluate_git_contains_change,
    _evaluate_manual,
    get_audit,
    list_audits,
    run_audit,
)
from riva.models import VerificationCriterion
from riva.schema import ensure_schema


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with some files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "service.py").write_text(
        "def handle_request():\n    return 'ok'\n\ndef other():\n    pass\n"
    )
    (ws / "README.md").write_text("# Test\n")
    return ws


@pytest.fixture
def db_with_contract(tmp_path):
    """Set up DB with a contract for audit testing."""
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    # Plan
    conn.execute(
        "INSERT INTO riva_plans "
        "(id, project_id, title, user_request, status, created_at, updated_at) "
        "VALUES ('plan-a1', 'proj-1', 'Test', 'test', 'approved', '2026-01-01', '2026-01-01')"
    )

    # Contract with mixed criteria
    criteria = json.dumps({
        "criteria": [
            {"type": "file_exists", "path": "src/service.py"},
            {"type": "file_exists", "path": "src/missing.py"},
            {"type": "function_defined", "file": "src/service.py", "name": "handle_request"},
            {"type": "manual_verification", "description": "tests pass"},
        ],
        "nol_assembly": "",
        "nol_intent_hash": "test",
        "nol_verified": False,
    })
    conn.execute(
        "INSERT INTO riva_contracts "
        "(id, plan_id, agent_id, verification_criteria_json, "
        "approved_at, approved_by, status, created_at, updated_at) "
        "VALUES ('contract-a1', 'plan-a1', 'agent-1', ?, "
        "'2026-01-01', 'user', 'active', '2026-01-01', '2026-01-01')",
        (criteria,),
    )
    conn.commit()
    conn.close()

    with patch("riva.db.settings") as mock_s, \
         patch("riva.audit_engine.get_connection") as mock_get, \
         patch("riva.audit_engine.transaction") as mock_tx, \
         patch("riva.contract_store.get_connection") as mock_get2, \
         patch("riva.contract_store.transaction") as mock_tx2:
        mock_s.data_dir = tmp_path

        def _get_conn(readonly=False):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            return c

        mock_get.side_effect = _get_conn
        mock_get2.side_effect = _get_conn

        from contextlib import contextmanager

        @contextmanager
        def _tx():
            c = _get_conn()
            try:
                yield c
                c.commit()
            finally:
                c.close()

        mock_tx.side_effect = _tx
        mock_tx2.side_effect = _tx

        yield tmp_path


class TestFileExistsEvaluator:
    """Tests for file_exists criterion."""

    def test_file_exists_passes(self, workspace):
        c = VerificationCriterion(type="file_exists", path="src/service.py")
        result = _evaluate_file_exists(workspace, c)
        assert result["status"] == "passed"

    def test_file_missing_fails(self, workspace):
        c = VerificationCriterion(type="file_exists", path="src/missing.py")
        result = _evaluate_file_exists(workspace, c)
        assert result["status"] == "failed"

    def test_no_path_inconclusive(self, workspace):
        c = VerificationCriterion(type="file_exists")
        result = _evaluate_file_exists(workspace, c)
        assert result["status"] == "inconclusive"


class TestFunctionDefinedEvaluator:
    """Tests for function_defined criterion."""

    def test_function_found(self, workspace):
        c = VerificationCriterion(
            type="function_defined", file="src/service.py", name="handle_request"
        )
        result = _evaluate_function_defined(workspace, c)
        assert result["status"] == "passed"
        assert "handle_request" in result["evidence"]

    def test_function_not_found(self, workspace):
        c = VerificationCriterion(
            type="function_defined", file="src/service.py", name="nonexistent_func"
        )
        result = _evaluate_function_defined(workspace, c)
        assert result["status"] == "failed"

    def test_file_not_found(self, workspace):
        c = VerificationCriterion(
            type="function_defined", file="src/missing.py", name="func"
        )
        result = _evaluate_function_defined(workspace, c)
        assert result["status"] == "failed"

    def test_missing_params_inconclusive(self, workspace):
        c = VerificationCriterion(type="function_defined")
        result = _evaluate_function_defined(workspace, c)
        assert result["status"] == "inconclusive"


class TestGitEvaluators:
    """Tests for git-based evaluators."""

    @patch("riva.audit_engine.subprocess.run")
    def test_git_contains_change_passed(self, mock_run, workspace):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="src/service.py\nsrc/routes.py\n"
        )
        c = VerificationCriterion(type="git_contains_change", path="src/service.py")
        result = _evaluate_git_contains_change(workspace, c)
        assert result["status"] == "passed"

    @patch("riva.audit_engine.subprocess.run")
    def test_git_contains_change_failed(self, mock_run, workspace):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="README.md\n"
        )
        c = VerificationCriterion(type="git_contains_change", path="src/")
        result = _evaluate_git_contains_change(workspace, c)
        assert result["status"] == "failed"

    @patch("riva.audit_engine.subprocess.run")
    def test_git_nonzero_inconclusive(self, mock_run, workspace):
        """Non-zero git return code -> inconclusive, not failed."""
        mock_run.return_value = MagicMock(
            returncode=128, stdout="", stderr="fatal: not a git repo"
        )
        c = VerificationCriterion(type="git_contains_change", path="src/")
        result = _evaluate_git_contains_change(workspace, c)
        assert result["status"] == "inconclusive"

    @patch("riva.audit_engine.subprocess.run")
    def test_git_commit_message_found(self, mock_run, workspace):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="abc1234 feat: add authentication\ndef5678 fix: typo\n",
        )
        c = VerificationCriterion(type="git_commit_message", keyword="authentication")
        result = _evaluate_git_commit_message(workspace, c)
        assert result["status"] == "passed"

    @patch("riva.audit_engine.subprocess.run")
    def test_git_commit_message_not_found(self, mock_run, workspace):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="abc1234 fix: typo\n"
        )
        c = VerificationCriterion(type="git_commit_message", keyword="authentication")
        result = _evaluate_git_commit_message(workspace, c)
        assert result["status"] == "failed"


class TestManualEvaluator:
    """Tests for manual_verification criterion."""

    def test_always_inconclusive(self):
        c = VerificationCriterion(
            type="manual_verification", description="tests pass"
        )
        result = _evaluate_manual(c)
        assert result["status"] == "inconclusive"
        assert "tests pass" in result["evidence"]


class TestRunAudit:
    """Integration tests for run_audit."""

    def test_mixed_results_partial(self, db_with_contract, workspace):
        """Contract with mixed pass/fail criteria -> partial verdict."""
        result = run_audit("contract-a1", str(workspace), triggered_by="user")

        assert result["overall_verdict"] == "partial"
        assert result["audit_id"].startswith("audit-")
        assert len(result["criteria_results"]) == 4

        # Check individual results
        statuses = [r["status"] for r in result["criteria_results"]]
        assert "passed" in statuses  # src/service.py exists
        assert "failed" in statuses  # src/missing.py doesn't exist
        assert "inconclusive" in statuses  # manual check

    def test_audit_persisted(self, db_with_contract, workspace):
        """Audit result is persisted in the database."""
        result = run_audit("contract-a1", str(workspace))
        audit = get_audit(result["audit_id"])
        assert audit is not None
        assert audit["overall_verdict"] == result["overall_verdict"]

    def test_all_pass(self, db_with_contract, tmp_path):
        """All-pass criteria -> passed verdict."""
        # Create workspace with all required files
        ws = tmp_path / "full_ws"
        ws.mkdir()
        (ws / "src").mkdir()
        (ws / "src" / "service.py").write_text("def handle_request(): pass\n")
        (ws / "src" / "missing.py").write_text("# exists now\n")

        # Need a separate plan + contract with only file-based criteria
        conn = sqlite3.connect(str(tmp_path / "talkingrock.db"))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO riva_plans "
            "(id, project_id, title, user_request, status, created_at, updated_at) "
            "VALUES ('plan-a2', 'proj-1', 'All Pass', 'test', "
            "'approved', '2026-01-01', '2026-01-01')"
        )
        criteria = json.dumps({
            "criteria": [
                {"type": "file_exists", "path": "src/service.py"},
                {"type": "file_exists", "path": "src/missing.py"},
            ],
            "nol_assembly": "",
            "nol_intent_hash": "",
            "nol_verified": False,
        })
        conn.execute(
            "INSERT INTO riva_contracts "
            "(id, plan_id, agent_id, verification_criteria_json, "
            "approved_at, approved_by, status, created_at, updated_at) "
            "VALUES ('contract-a2', 'plan-a2', 'agent-1', ?, "
            "'2026-01-01', 'user', 'active', '2026-01-01', '2026-01-01')",
            (criteria,),
        )
        conn.commit()
        conn.close()

        result = run_audit("contract-a2", str(ws))
        assert result["overall_verdict"] == "passed"

    def test_list_audits(self, db_with_contract, workspace):
        """list_audits returns persisted audits."""
        run_audit("contract-a1", str(workspace))
        audits = list_audits(contract_id="contract-a1")
        assert len(audits) == 1

    def test_nonexistent_contract(self, db_with_contract, workspace):
        """run_audit raises for nonexistent contract."""
        from riva.errors import AuditError

        with pytest.raises(AuditError):
            run_audit("contract-nonexistent", str(workspace))
