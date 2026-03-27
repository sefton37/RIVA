"""Tests for the RIVA play write integration."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from riva.play_write import confirm_scene_complete, propose_scene_update
from riva.schema import ensure_schema


@pytest.fixture
def db_with_play(tmp_path):
    """Set up DB with RIVA tables + Cairn Play tables (acts, scenes)."""
    db_path = tmp_path / "talkingrock.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)

    # Create acts table (Cairn's Play schema)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS acts (
            act_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            repo_path TEXT,
            artifact_type TEXT,
            system_role TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Create scenes table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scenes (
            scene_id TEXT PRIMARY KEY,
            act_id TEXT NOT NULL REFERENCES acts(act_id),
            title TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'planning',
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Insert test data
    conn.execute(
        "INSERT INTO acts (act_id, title, active, position, created_at, updated_at) "
        "VALUES ('act-1', 'Auth Project', 1, 0, '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO scenes (scene_id, act_id, title, stage, position, created_at, updated_at) "
        "VALUES ('scene-1', 'act-1', 'Build login', 'in_progress', 0, '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO scenes (scene_id, act_id, title, stage, position, created_at, updated_at) "
        "VALUES ('scene-2', 'act-1', 'Add tests', 'planning', 1, '2026-01-01', '2026-01-01')"
    )

    # RIVA project linked to Act
    conn.execute(
        "INSERT INTO riva_projects "
        "(id, name, description, act_id, status, created_at, updated_at) "
        "VALUES ('proj-1', 'Auth', 'desc', 'act-1', 'active', '2026-01-01', '2026-01-01')"
    )

    # Plan + contract
    conn.execute(
        "INSERT INTO riva_plans "
        "(id, project_id, title, user_request, status, created_at, updated_at) "
        "VALUES ('plan-pw1', 'proj-1', 'Plan', 'req', 'approved', '2026-01-01', '2026-01-01')"
    )

    criteria = json.dumps({
        "criteria": [], "nol_assembly": "",
        "nol_intent_hash": "", "nol_verified": False,
    })
    conn.execute(
        "INSERT INTO riva_contracts "
        "(id, plan_id, agent_id, verification_criteria_json, "
        "approved_at, approved_by, status, created_at, updated_at) "
        "VALUES ('contract-pw1', 'plan-pw1', 'agent-1', ?, "
        "'2026-01-01', 'user', 'active', '2026-01-01', '2026-01-01')",
        (criteria,),
    )

    conn.commit()
    conn.close()

    with patch("riva.db.settings") as mock_s, \
         patch("riva.play_write.get_connection") as mock_get, \
         patch("riva.play_write.transaction") as mock_tx:
        mock_s.data_dir = tmp_path

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

        yield tmp_path


class TestProposeSceneUpdate:
    """Tests for propose_scene_update."""

    def test_proposes_for_linked_project(self, db_with_play):
        """Proposes scene update when project is linked to Act."""
        proposal = propose_scene_update("contract-pw1", "audit-1")
        assert proposal is not None
        assert proposal["type"] == "scene_complete"
        assert len(proposal["scenes"]) == 1  # Only in_progress scenes
        assert proposal["scenes"][0]["title"] == "Build login"

    def test_no_proposal_without_act_link(self, db_with_play):
        """Returns None when project has no Act linkage."""
        # Create unlinked project + plan + contract
        conn = sqlite3.connect(str(db_with_play / "talkingrock.db"))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO riva_projects "
            "(id, name, description, status, created_at, updated_at) "
            "VALUES ('proj-2', 'No Link', '', 'active', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO riva_plans "
            "(id, project_id, title, user_request, status, created_at, updated_at) "
            "VALUES ('plan-pw2', 'proj-2', 'Plan', 'req', 'approved', "
            "'2026-01-01', '2026-01-01')"
        )
        criteria = json.dumps({
            "criteria": [], "nol_assembly": "",
            "nol_intent_hash": "", "nol_verified": False,
        })
        conn.execute(
            "INSERT INTO riva_contracts "
            "(id, plan_id, agent_id, verification_criteria_json, "
            "approved_at, approved_by, status, created_at, updated_at) "
            "VALUES ('contract-pw2', 'plan-pw2', 'agent-1', ?, "
            "'2026-01-01', 'user', 'active', '2026-01-01', '2026-01-01')",
            (criteria,),
        )
        conn.commit()
        conn.close()

        proposal = propose_scene_update("contract-pw2", "audit-2")
        assert proposal is None

    def test_no_proposal_nonexistent_contract(self, db_with_play):
        """Returns None for nonexistent contract."""
        proposal = propose_scene_update("contract-nonexistent", "audit-1")
        assert proposal is None


class TestConfirmSceneComplete:
    """Tests for confirm_scene_complete."""

    def test_marks_scene_complete(self, db_with_play):
        """Marks an in_progress scene as complete."""
        result = confirm_scene_complete("scene-1")
        assert result["stage"] == "complete"

        # Verify in DB
        conn = sqlite3.connect(str(db_with_play / "talkingrock.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT stage FROM scenes WHERE scene_id='scene-1'"
        ).fetchone()
        conn.close()
        assert row["stage"] == "complete"

    def test_rejects_non_in_progress(self, db_with_play):
        """Raises error for scene not in in_progress stage."""
        from riva.errors import RivaError

        with pytest.raises(RivaError):
            confirm_scene_complete("scene-2")  # planning stage

    def test_rejects_nonexistent(self, db_with_play):
        """Raises error for nonexistent scene."""
        from riva.errors import RivaError

        with pytest.raises(RivaError):
            confirm_scene_complete("scene-nonexistent")
