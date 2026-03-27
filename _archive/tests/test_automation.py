"""Tests for the automation loops module."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from riva.automation import (
    _close_issue,
    _find_linked_issue,
    _guess_repo_from_cwd,
    on_audit_passed,
    set_devops_clients,
)
from riva.schema import ensure_schema


@pytest.fixture
def db_setup(tmp_path):
    """Set up test database with schema, patch pm_store DB access."""
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.close()

    with patch("riva.pm_store.get_connection") as mock_get, \
         patch("riva.pm_store.transaction") as mock_tx:

        def _get_conn(readonly=False):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            return c

        mock_get.side_effect = _get_conn

        @contextmanager
        def _tx():
            c = _get_conn()
            try:
                yield c
                c.commit()
            finally:
                c.close()

        mock_tx.side_effect = _tx
        yield tmp_path


class TestGuessRepo:

    def test_cairn(self):
        assert _guess_repo_from_cwd("/home/user/dev/Cairn") == "cairn"

    def test_reos(self):
        assert _guess_repo_from_cwd("/home/user/dev/ReOS") == "ReOS"

    def test_riva(self):
        assert _guess_repo_from_cwd("/home/user/dev/RIVA") == "RIVA"

    def test_unknown(self):
        assert _guess_repo_from_cwd("/tmp/random") is None

    def test_nested_path(self):
        assert _guess_repo_from_cwd("/home/user/dev/talkingrock-core/src") == "talkingrock-core"


def _insert_dummy_contract(db_path, contract_id: str) -> None:
    """Insert a minimal contract row to satisfy FK constraints."""
    import sqlite3 as _sq
    c = _sq.connect(str(db_path))
    c.execute("PRAGMA foreign_keys=ON")
    # Need a plan first (contract FK → plan)
    plan_id = f"plan-{contract_id[-12:]}"
    now = "2026-01-01T00:00:00"
    c.execute(
        "INSERT OR IGNORE INTO riva_plans (id, project_id, title, user_request, status, created_at, updated_at) "
        "VALUES (?, 'proj-test', 'test', 'test', 'approved', ?, ?)",
        (plan_id, now, now),
    )
    c.execute(
        "INSERT OR IGNORE INTO riva_contracts (id, plan_id, agent_id, approved_at, status, created_at, updated_at) "
        "VALUES (?, ?, 'agent-test', ?, 'active', ?, ?)",
        (contract_id, plan_id, now, now, now),
    )
    c.commit()
    c.close()


class TestFindLinkedIssue:

    def test_finds_linked_issue(self, db_setup):
        from riva.pm_store import create_epic, create_issue

        _insert_dummy_contract(db_setup / "talkingrock.db", "contract-abc123")

        epic = create_epic("Test", act_id="act-test")
        issue = create_issue("Linked Task", epic_id=epic.id, riva_contract_id="contract-abc123")
        found = _find_linked_issue("contract-abc123")
        assert found == issue.id

    def test_returns_none_when_not_found(self, db_setup):
        assert _find_linked_issue("contract-nonexistent") is None


class TestCloseIssue:

    def test_closes_issue(self, db_setup):
        from riva.pm_store import create_epic, create_issue, get_issue

        epic = create_epic("Test", act_id="act-test")
        issue = create_issue("Open Task", epic_id=epic.id)
        assert issue.status == "Backlog"

        _close_issue(issue.id, "contract-123")
        updated = get_issue(issue.id)
        assert updated is not None
        assert updated.status == "Done"


class TestOnAuditPassed:

    def test_full_chain_no_devops(self, db_setup):
        """Without DevOps configured, should still find issue and close it."""
        from riva.pm_store import create_epic, create_issue, get_issue

        set_devops_clients(None, None)
        _insert_dummy_contract(db_setup / "talkingrock.db", "contract-test1")

        epic = create_epic("Test", act_id="act-test")
        issue = create_issue("Task", epic_id=epic.id, riva_contract_id="contract-test1")

        summary = on_audit_passed("contract-test1", "audit-1", "/home/user/dev/Cairn")

        assert summary["steps"]["find_issue"]["issue_id"] == issue.id
        assert summary["steps"]["create_pr"]["skipped"] is True

        # Without CI, issue should be closed directly
        updated = get_issue(issue.id)
        assert updated is not None
        assert updated.status == "Done"

    def test_chain_with_forgejo_pr(self, db_setup):
        """With Forgejo configured, should attempt PR creation."""
        mock_forgejo = MagicMock()
        mock_forgejo.configured = True
        mock_forgejo.create_pull.return_value = {"number": 42}

        set_devops_clients(mock_forgejo, None)
        _insert_dummy_contract(db_setup / "talkingrock.db", "contract-test2")

        from riva.pm_store import create_epic, create_issue

        epic = create_epic("Test", act_id="act-test")
        create_issue("Task", epic_id=epic.id, riva_contract_id="contract-test2")

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "feature-branch\n"
            mock_run.return_value = mock_result

            summary = on_audit_passed("contract-test2", "audit-2", "/home/user/dev/Cairn")

        assert summary["steps"]["create_pr"]["pr_number"] == 42
        mock_forgejo.create_pull.assert_called_once()

        set_devops_clients(None, None)

    def test_chain_no_linked_issue(self, db_setup):
        """No linked issue should not error."""
        set_devops_clients(None, None)
        summary = on_audit_passed("contract-orphan", "audit-3", "/tmp/random")
        assert summary["steps"]["find_issue"]["issue_id"] is None
        assert summary["steps"]["ci_poll"]["skipped"] is True
