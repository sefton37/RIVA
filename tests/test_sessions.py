"""Tests for the RIVA session RPC handlers."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from riva.rpc_handlers.sessions import (
    _build_dispatch_prompt,
    handle_session_history,
    handle_session_poll,
)
from riva.schema import ensure_schema


@pytest.fixture
def db_with_contract(tmp_path):
    """Set up DB with plan, steps, and contract."""
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    # Insert plan
    conn.execute(
        "INSERT INTO riva_plans "
        "(id, project_id, title, user_request, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("plan-s1", "proj-1", "Add Auth", "Build authentication",
         "approved", "2026-01-01", "2026-01-01"),
    )

    # Insert steps
    conn.execute(
        "INSERT INTO riva_plan_steps "
        "(id, plan_id, step_number, title, description, "
        "acceptance_criterion, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("step-s1", "plan-s1", 1, "Create models", "Define User model",
         "file_exists: src/models.py", "pending", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO riva_plan_steps "
        "(id, plan_id, step_number, title, description, "
        "acceptance_criterion, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("step-s2", "plan-s1", 2, "Add endpoint", "POST /login",
         "function_defined: src/routes.py::login", "pending",
         "2026-01-01", "2026-01-01"),
    )

    # Insert contract
    criteria = json.dumps({
        "criteria": [
            {"type": "file_exists", "path": "src/models.py"},
            {"type": "function_defined", "file": "src/routes.py", "name": "login"},
        ],
        "nol_assembly": "; INTENT: Add Auth\nHALT\n",
        "nol_intent_hash": "abc123",
        "nol_verified": True,
    })
    conn.execute(
        "INSERT INTO riva_contracts "
        "(id, plan_id, agent_id, verification_criteria_json, "
        "approved_at, approved_by, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("contract-s1", "plan-s1", "agent-1", criteria,
         "2026-01-01", "user", "active", "2026-01-01", "2026-01-01"),
    )

    conn.commit()
    conn.close()

    with patch("riva.db.settings") as mock_s, \
         patch("riva.rpc_handlers.sessions.get_connection") as mock_get, \
         patch("riva.rpc_handlers.sessions.transaction") as mock_tx, \
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

        yield db_path


class TestDispatchPrompt:
    """Tests for contract dispatch prompt generation."""

    def test_prompt_contains_title(self, db_with_contract):
        """Dispatch prompt contains plan title."""
        prompt = _build_dispatch_prompt("contract-s1")
        assert "Add Auth" in prompt

    def test_prompt_contains_steps(self, db_with_contract):
        """Dispatch prompt contains step titles."""
        prompt = _build_dispatch_prompt("contract-s1")
        assert "Create models" in prompt
        assert "Add endpoint" in prompt

    def test_prompt_contains_criteria(self, db_with_contract):
        """Dispatch prompt contains acceptance criteria."""
        prompt = _build_dispatch_prompt("contract-s1")
        assert "file_exists" in prompt
        assert "function_defined" in prompt

    def test_prompt_contains_user_request(self, db_with_contract):
        """Dispatch prompt includes original user request."""
        prompt = _build_dispatch_prompt("contract-s1")
        assert "Build authentication" in prompt

    def test_nonexistent_contract(self, db_with_contract):
        """Raises error for nonexistent contract."""
        from riva.errors import RivaError

        with pytest.raises(RivaError):
            _build_dispatch_prompt("contract-nonexistent")


class TestSessionPoll:
    """Tests for session poll handler."""

    def test_poll_returns_events(self):
        """poll wraps CCManager.poll_events."""
        from riva.rpc_handlers import sessions

        mock_mgr = MagicMock()
        mock_mgr.poll_events.return_value = {
            "events": [{"type": "assistant_delta", "text": "Hi"}],
            "next_index": 1,
            "busy": True,
        }
        sessions._manager = mock_mgr

        result = handle_session_poll(agent_id="agent-1", since=0)

        assert result["events"][0]["text"] == "Hi"
        assert result["busy"] is True
        mock_mgr.poll_events.assert_called_once_with("agent-1", since=0)

        sessions._manager = None  # cleanup

    def test_poll_requires_agent_id(self):
        """Missing agent_id raises error."""
        from riva.errors import RivaError

        with pytest.raises(RivaError):
            handle_session_poll(agent_id="")


class TestSessionHistory:
    """Tests for session history handler."""

    def test_history_wraps_manager(self):
        """history wraps CCManager.get_history."""
        from riva.rpc_handlers import sessions

        mock_mgr = MagicMock()
        mock_mgr.get_history.return_value = [
            {"role": "user", "content": "hello", "created_at": "2026-01-01"},
        ]
        sessions._manager = mock_mgr

        result = handle_session_history(agent_id="agent-1", limit=50)

        assert len(result["history"]) == 1
        assert result["history"][0]["role"] == "user"
        mock_mgr.get_history.assert_called_once_with("agent-1", limit=50)

        sessions._manager = None
