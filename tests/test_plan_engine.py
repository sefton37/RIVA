"""Tests for the RIVA plan engine."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from riva.plan_engine import PlanEngine
from riva.schema import ensure_schema


@pytest.fixture
def db_setup(tmp_path):
    """Set up a test database."""
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.close()

    with patch("riva.db.settings") as mock_settings, \
         patch("riva.plan_engine.get_connection") as mock_get, \
         patch("riva.plan_engine.transaction") as mock_tx:
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

        yield db_path


@pytest.fixture
def mock_provider():
    """Mock LLM provider that returns valid plan JSON."""
    provider = MagicMock()
    provider.chat_json.return_value = json.dumps({
        "title": "Add User Authentication",
        "steps": [
            {
                "step_number": 1,
                "title": "Create user model",
                "description": "Define SQLAlchemy User model",
                "acceptance_criterion": "file_exists: src/models.py",
                "estimated_minutes": 15,
            },
            {
                "step_number": 2,
                "title": "Add login endpoint",
                "description": "POST /auth/login route",
                "acceptance_criterion": "function_defined: src/routes.py::login",
                "estimated_minutes": 20,
            },
        ],
        "risks": ["Insecure password storage"],
        "estimated_minutes": 35,
    })
    return provider


class TestPlanEngine:
    """Tests for PlanEngine."""

    def test_decompose_returns_plan_id(self, db_setup):
        """decompose() returns a plan_id string immediately."""
        engine = PlanEngine(provider=None)  # No provider = no async
        plan_id = engine.decompose("proj-1", "Add user auth")

        assert plan_id.startswith("plan-")
        assert len(plan_id) > 5

    def test_draft_plan_in_db(self, db_setup):
        """After decompose(), a draft plan exists in the DB."""
        engine = PlanEngine(provider=None)
        plan_id = engine.decompose("proj-1", "Add user auth")

        plan = engine.get_plan(plan_id)
        assert plan is not None
        assert plan.status == "draft"
        assert plan.user_request == "Add user auth"
        assert plan.project_id == "proj-1"

    def test_get_nonexistent_plan(self, db_setup):
        """get_plan() returns None for unknown plan_id."""
        engine = PlanEngine(provider=None)
        assert engine.get_plan("plan-nonexistent") is None

    def test_save_plan_from_json(self, db_setup, mock_provider):
        """_save_plan correctly populates steps."""
        engine = PlanEngine(provider=mock_provider)
        plan_id = engine.decompose("proj-1", "Add user auth")

        # Simulate what the background task does
        raw = mock_provider.chat_json.return_value
        parsed = json.loads(raw)
        engine._save_plan(plan_id, parsed, raw)

        plan = engine.get_plan(plan_id)
        assert plan is not None
        assert plan.status == "pending_approval"
        assert plan.title == "Add User Authentication"
        assert len(plan.steps) == 2
        assert plan.steps[0].title == "Create user model"
        assert plan.steps[0].acceptance_criterion == "file_exists: src/models.py"
        assert plan.steps[1].step_number == 2
        assert plan.estimated_minutes == 35
        assert plan.risks == ["Insecure password storage"]

    def test_save_error(self, db_setup):
        """_save_error marks plan as failed."""
        engine = PlanEngine(provider=None)
        plan_id = engine.decompose("proj-1", "test")

        engine._save_error(plan_id, "LLM timeout")

        plan = engine.get_plan(plan_id)
        assert plan.status == "failed"
        assert "LLM timeout" in plan.decomposition_json

    def test_list_plans(self, db_setup, mock_provider):
        """list_plans returns plans for a project."""
        engine = PlanEngine(provider=mock_provider)

        # Create two plans
        engine.decompose("proj-1", "First task")
        engine.decompose("proj-1", "Second task")
        engine.decompose("proj-2", "Other project")

        plans = engine.list_plans("proj-1")
        assert len(plans) == 2

    def test_plan_to_dict(self, db_setup, mock_provider):
        """Plan.to_dict() produces JSON-serializable output."""
        engine = PlanEngine(provider=mock_provider)
        plan_id = engine.decompose("proj-1", "test")

        raw = mock_provider.chat_json.return_value
        engine._save_plan(plan_id, json.loads(raw), raw)

        plan = engine.get_plan(plan_id)
        d = plan.to_dict()
        # Should be JSON-serializable
        json.dumps(d)
        assert d["title"] == "Add User Authentication"
        assert len(d["steps"]) == 2
