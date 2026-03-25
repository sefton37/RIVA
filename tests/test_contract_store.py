"""Tests for the RIVA contract store."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from riva.contract_store import (
    _parse_criterion_from_text,
    cancel_contract,
    create_contract,
    get_contract,
    list_contracts,
)
from riva.errors import ContractError
from riva.schema import ensure_schema


@pytest.fixture
def db_with_plan(tmp_path):
    """Set up DB with a pending_approval plan and steps."""
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    # Insert a plan
    conn.execute(
        "INSERT INTO riva_plans "
        "(id, project_id, title, user_request, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("plan-test1", "proj-1", "Test Plan", "Build a thing",
         "pending_approval", "2026-01-01", "2026-01-01"),
    )

    # Insert steps with verifiable acceptance criteria
    conn.execute(
        "INSERT INTO riva_plan_steps "
        "(id, plan_id, step_number, title, acceptance_criterion, "
        "status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("step-1", "plan-test1", 1, "Create service",
         "file_exists: src/service.py", "pending", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO riva_plan_steps "
        "(id, plan_id, step_number, title, acceptance_criterion, "
        "status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("step-2", "plan-test1", 2, "Add handler",
         "function_defined: src/service.py::handle_request",
         "pending", "2026-01-01", "2026-01-01"),
    )
    conn.commit()
    conn.close()

    with patch("riva.db.settings") as mock_settings, \
         patch("riva.contract_store.get_connection") as mock_get, \
         patch("riva.contract_store.transaction") as mock_tx, \
         patch("riva.contract_store.create_nol_contract") as mock_nol:
        mock_settings.data_dir = tmp_path

        def _get_conn(readonly=False):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            return c

        mock_get.side_effect = _get_conn

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

        # Mock NOL contract creation
        from riva.nol_contract import NolContractResult

        mock_nol.return_value = NolContractResult(
            assembly=(
                "; INTENT: Test Plan\n; POST[0]: file_exists:"
                " src/service.py\nCONST I64 0 0\nHALT\n"
            ),
            intent_hash="abc123def456",
            verified=True,
        )

        yield db_path


class TestCriterionParsing:
    """Tests for _parse_criterion_from_text."""

    def test_explicit_file_exists(self):
        c = _parse_criterion_from_text("file_exists: src/service.py")
        assert c.type == "file_exists"
        assert c.path == "src/service.py"

    def test_explicit_function_defined(self):
        c = _parse_criterion_from_text("function_defined: src/routes.py::login")
        assert c.type == "function_defined"
        assert c.file == "src/routes.py"
        assert c.name == "login"

    def test_explicit_git_contains_change(self):
        c = _parse_criterion_from_text("git_contains_change: src/")
        assert c.type == "git_contains_change"
        assert c.path == "src/"

    def test_explicit_git_commit_message(self):
        c = _parse_criterion_from_text("git_commit_message: add auth")
        assert c.type == "git_commit_message"
        assert c.keyword == "add auth"

    def test_natural_file_exists(self):
        c = _parse_criterion_from_text("src/models.py exists")
        assert c.type == "file_exists"
        assert c.path == "src/models.py"

    def test_natural_defined_in(self):
        c = _parse_criterion_from_text("login defined in src/routes.py")
        assert c.type == "function_defined"
        assert c.name == "login"
        assert c.file == "src/routes.py"

    def test_fallback_to_manual(self):
        c = _parse_criterion_from_text("everything works correctly")
        assert c.type == "manual_verification"
        assert c.description == "everything works correctly"


class TestContractStore:
    """Tests for contract creation and management."""

    def test_create_contract(self, db_with_plan):
        """create_contract produces a valid contract with criteria and NOL."""
        contract = create_contract("plan-test1", "agent-1")

        assert contract.id.startswith("contract-")
        assert contract.plan_id == "plan-test1"
        assert contract.agent_id == "agent-1"
        assert contract.status == "active"
        assert contract.approved_by == "user"
        assert len(contract.verification_criteria) == 2
        assert contract.verification_criteria[0].type == "file_exists"
        assert contract.verification_criteria[1].type == "function_defined"

        # NOL data
        assert contract.nol_assembly is not None
        assert "POST[0]" in contract.nol_assembly
        assert contract.nol_intent_hash == "abc123def456"
        assert contract.nol_verified is True

    def test_create_contract_nonexistent_plan(self, db_with_plan):
        """Creating contract for nonexistent plan raises error."""
        with pytest.raises(ContractError, match="Plan not found"):
            create_contract("plan-nonexistent", "agent-1")

    def test_get_contract(self, db_with_plan):
        """get_contract retrieves a stored contract."""
        contract = create_contract("plan-test1", "agent-1")
        retrieved = get_contract(contract.id)

        assert retrieved is not None
        assert retrieved.id == contract.id
        assert retrieved.plan_id == "plan-test1"
        assert len(retrieved.verification_criteria) == 2
        assert retrieved.nol_verified is True

    def test_get_nonexistent_contract(self, db_with_plan):
        """get_contract returns None for unknown ID."""
        assert get_contract("contract-nonexistent") is None

    def test_list_contracts(self, db_with_plan):
        """list_contracts returns active contracts."""
        create_contract("plan-test1", "agent-1")
        contracts = list_contracts()
        assert len(contracts) == 1

    def test_cancel_contract(self, db_with_plan):
        """cancel_contract marks it as cancelled."""
        contract = create_contract("plan-test1", "agent-1")
        cancel_contract(contract.id)

        retrieved = get_contract(contract.id)
        assert retrieved.status == "cancelled"

    def test_cancel_nonexistent_contract(self, db_with_plan):
        """Cancelling nonexistent contract raises error."""
        with pytest.raises(ContractError):
            cancel_contract("contract-nonexistent")

    def test_contract_to_dict(self, db_with_plan):
        """Contract.to_dict() is JSON-serializable."""
        contract = create_contract("plan-test1", "agent-1")
        d = contract.to_dict()
        json.dumps(d)  # Should not raise
        assert d["nol_verified"] is True
        assert d["nol_intent_hash"] == "abc123def456"
        assert len(d["verification_criteria"]) == 2

    def test_plan_status_updated_on_approve(self, db_with_plan):
        """Plan status changes to 'approved' when contract is created."""
        create_contract("plan-test1", "agent-1")

        # Check plan status in DB
        db_path = db_with_plan
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, agent_id FROM riva_plans WHERE id='plan-test1'"
        ).fetchone()
        conn.close()

        assert row["status"] == "approved"
        assert row["agent_id"] == "agent-1"
