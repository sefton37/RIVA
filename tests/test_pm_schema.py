"""Tests for PM table schema (FK, PK, NULL constraints)."""

from __future__ import annotations

import sqlite3

import pytest

from riva.schema import ensure_schema


@pytest.fixture
def conn(tmp_path):
    """Fresh DB with RIVA + PM schema."""
    db_path = tmp_path / "test.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    ensure_schema(c)
    yield c
    c.close()


class TestPmTablesExist:

    def test_all_pm_tables_created(self, conn):
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pm_%'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        expected = {
            "pm_epics",
            "pm_cycles",
            "pm_issues",
            "pm_cycle_issues",
            "pm_roadmap",
            "pm_roadmap_epics",
            "pm_research",
        }
        assert tables == expected

    def test_pm_epics_has_act_id(self, conn):
        cursor = conn.execute("PRAGMA table_info(pm_epics)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "act_id" in columns

class TestPmForeignKeys:

    def test_issue_epic_fk_enforced(self, conn):
        """Issue with nonexistent epic_id should fail."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pm_issues (id, name, status, priority, type, "
                "epic_id, created_at, updated_at) "
                "VALUES ('i1', 'test', 'Backlog', 'Medium', 'Feature', "
                "'nonexistent', '2026-01-01', '2026-01-01')"
            )

    def test_issue_cycle_fk_enforced(self, conn):
        """Issue with nonexistent cycle_id should fail."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pm_issues (id, name, status, priority, type, "
                "cycle_id, created_at, updated_at) "
                "VALUES ('i1', 'test', 'Backlog', 'Medium', 'Feature', "
                "'nonexistent', '2026-01-01', '2026-01-01')"
            )

    def test_issue_null_fks_allowed(self, conn):
        """Issues with NULL FKs should insert fine."""
        conn.execute(
            "INSERT INTO pm_issues (id, name, status, priority, type, "
            "created_at, updated_at) "
            "VALUES ('i1', 'test', 'Backlog', 'Medium', 'Feature', "
            "'2026-01-01', '2026-01-01')"
        )
        row = conn.execute("SELECT * FROM pm_issues WHERE id='i1'").fetchone()
        assert row is not None
        assert row["epic_id"] is None
        assert row["cycle_id"] is None

    def test_research_epic_fk_enforced(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pm_research (id, name, status, epic_id, "
                "created_at, updated_at) "
                "VALUES ('r1', 'test', 'In Progress', 'nonexistent', "
                "'2026-01-01', '2026-01-01')"
            )

    def test_research_issue_fk_enforced(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pm_research (id, name, status, issue_id, "
                "created_at, updated_at) "
                "VALUES ('r1', 'test', 'In Progress', 'nonexistent', "
                "'2026-01-01', '2026-01-01')"
            )

    def test_cycle_issues_fk_enforced(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pm_cycle_issues (cycle_id, issue_id) "
                "VALUES ('nonexistent', 'nonexistent')"
            )

    def test_roadmap_epics_fk_enforced(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pm_roadmap_epics (roadmap_id, epic_id) "
                "VALUES ('nonexistent', 'nonexistent')"
            )


class TestPmCompositePKs:

    def test_cycle_issues_pk_prevents_duplicates(self, conn):
        conn.execute(
            "INSERT INTO pm_cycles (id, name, status, created_at, updated_at) "
            "VALUES ('c1', 'Sprint', 'Active', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO pm_issues (id, name, status, priority, type, "
            "created_at, updated_at) "
            "VALUES ('i1', 'task', 'Backlog', 'Medium', 'Feature', "
            "'2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO pm_cycle_issues (cycle_id, issue_id) VALUES ('c1', 'i1')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pm_cycle_issues (cycle_id, issue_id) VALUES ('c1', 'i1')"
            )

    def test_roadmap_epics_pk_prevents_duplicates(self, conn):
        conn.execute(
            "INSERT INTO pm_roadmap (id, name, status, created_at, updated_at) "
            "VALUES ('r1', 'Q2', 'Idea', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO pm_epics (id, name, status, priority, created_at, updated_at) "
            "VALUES ('e1', 'Epic', 'Backlog', 'Medium', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO pm_roadmap_epics (roadmap_id, epic_id) VALUES ('r1', 'e1')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pm_roadmap_epics (roadmap_id, epic_id) VALUES ('r1', 'e1')"
            )
