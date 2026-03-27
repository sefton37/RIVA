"""Tests for RIVA schema migration."""

from __future__ import annotations

import sqlite3

import pytest

from riva.schema import ensure_schema


class TestSchema:
    """Tests for ensure_schema()."""

    def test_creates_all_tables(self, tmp_path):
        """All riva_* and pm_* tables are created."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        ensure_schema(conn)

        # riva_projects is the only riva_* table now
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'riva_%'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert tables == {"riva_projects"}

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

    def test_riva_projects_columns(self, tmp_path):
        """riva_projects has expected columns."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        ensure_schema(conn)

        cursor = conn.execute("PRAGMA table_info(riva_projects)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "id" in columns
        assert "name" in columns
        assert "description" in columns
        assert "act_id" in columns
        assert "status" in columns

        conn.close()

    def test_pm_issues_no_riva_contract_id(self, tmp_path):
        """pm_issues no longer has riva_contract_id column."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        ensure_schema(conn)

        cursor = conn.execute("PRAGMA table_info(pm_issues)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "riva_contract_id" not in columns

        conn.close()
