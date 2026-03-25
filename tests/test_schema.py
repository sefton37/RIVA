"""Tests for RIVA schema migration."""

from __future__ import annotations

import sqlite3

import pytest

from riva.schema import ensure_schema


class TestSchema:
    """Tests for ensure_schema()."""

    def test_creates_all_tables(self, tmp_path):
        """All riva_* tables are created."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        ensure_schema(conn)

        # Query table names
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'riva_%'"
        )
        tables = {row[0] for row in cursor.fetchall()}

        expected = {
            "riva_projects",
            "riva_plans",
            "riva_plan_steps",
            "riva_contracts",
            "riva_audits",
            "riva_agent_properties",
            "riva_agent_sessions",
        }
        assert tables == expected

        # PM tables
        pm_cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pm_%'"
        )
        pm_tables = {row[0] for row in pm_cursor.fetchall()}
        pm_expected = {
            "pm_epics",
            "pm_cycles",
            "pm_issues",
            "pm_cycle_issues",
            "pm_roadmap",
            "pm_roadmap_epics",
            "pm_research",
        }
        assert pm_tables == pm_expected

        conn.close()

    def test_idempotent(self, tmp_path):
        """Running ensure_schema twice doesn't error."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        ensure_schema(conn)
        ensure_schema(conn)  # Should not raise

        conn.close()

    def test_tables_have_correct_columns(self, tmp_path):
        """Spot-check that key tables have expected columns."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        ensure_schema(conn)

        # Check riva_plans columns
        cursor = conn.execute("PRAGMA table_info(riva_plans)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "id" in columns
        assert "project_id" in columns
        assert "title" in columns
        assert "user_request" in columns
        assert "status" in columns
        assert "decomposition_json" in columns

        # Check riva_contracts columns
        cursor = conn.execute("PRAGMA table_info(riva_contracts)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "plan_id" in columns
        assert "agent_id" in columns
        assert "verification_criteria_json" in columns
        assert "status" in columns

        conn.close()

    def test_foreign_keys(self, tmp_path):
        """Foreign key constraints are present."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_schema(conn)

        # Insert a plan step with a non-existent plan_id should fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO riva_plan_steps (id, plan_id, step_number, title, "
                "status, created_at, updated_at) "
                "VALUES ('s1', 'nonexistent', 1, 'test', 'pending', '2026-01-01', '2026-01-01')"
            )

        conn.close()
