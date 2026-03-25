"""Tests for PM domain RPC handlers.

Tests the handler functions directly (not through the dispatcher).
DB access is patched at the pm_store level since handlers delegate there.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from riva.errors import PmError, RivaError
from riva.rpc_handlers.pm import (
    handle_cycles_add_issue,
    handle_cycles_create,
    handle_cycles_get,
    handle_cycles_issues,
    handle_cycles_list,
    handle_cycles_remove_issue,
    handle_cycles_update,
    handle_dashboard,
    handle_epics_archive,
    handle_epics_create,
    handle_epics_get,
    handle_epics_list,
    handle_epics_update,
    handle_issues_create,
    handle_issues_get,
    handle_issues_list,
    handle_issues_update,
    handle_research_create,
    handle_research_get,
    handle_research_list,
    handle_research_update,
    handle_roadmap_create,
    handle_roadmap_get,
    handle_roadmap_link_epic,
    handle_roadmap_list,
    handle_roadmap_unlink_epic,
    handle_roadmap_update,
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


# ── Epics ────────────────────────────────────────────────────────────


class TestEpicHandlers:

    def test_create_epic(self, db_setup):
        result = handle_epics_create(name="Test Epic", project="CAIRN", priority="High", act_id="act-test")
        assert result["id"].startswith("epic-")
        assert result["name"] == "Test Epic"
        assert result["project"] == "CAIRN"

    def test_create_requires_name(self, db_setup):
        with pytest.raises(RivaError, match="name is required"):
            handle_epics_create(name="", act_id="act-test")

    def test_list_epics(self, db_setup):
        handle_epics_create(name="E1", act_id="act-test")
        handle_epics_create(name="E2", act_id="act-test")
        result = handle_epics_list()
        assert len(result["epics"]) == 2

    def test_list_epics_filtered(self, db_setup):
        handle_epics_create(name="Active", status="Active", act_id="act-test")
        handle_epics_create(name="Backlog", act_id="act-test")
        result = handle_epics_list(status="Active")
        assert len(result["epics"]) == 1
        assert result["epics"][0]["name"] == "Active"

    def test_get_epic(self, db_setup):
        created = handle_epics_create(name="Find Me", act_id="act-test")
        result = handle_epics_get(epic_id=created["id"])
        assert result["name"] == "Find Me"

    def test_get_epic_not_found(self, db_setup):
        with pytest.raises(RivaError, match="Epic not found"):
            handle_epics_get(epic_id="epic-nonexistent")

    def test_get_epic_missing_id(self, db_setup):
        with pytest.raises(RivaError, match="epic_id is required"):
            handle_epics_get(epic_id="")

    def test_update_epic(self, db_setup):
        created = handle_epics_create(name="Original", act_id="act-test")
        result = handle_epics_update(epic_id=created["id"], name="Updated")
        assert result["name"] == "Updated"

    def test_update_epic_no_fields(self, db_setup):
        created = handle_epics_create(name="Test", act_id="act-test")
        with pytest.raises(RivaError, match="No fields to update"):
            handle_epics_update(epic_id=created["id"])

    def test_archive_epic(self, db_setup):
        created = handle_epics_create(name="To Archive", act_id="act-test")
        result = handle_epics_archive(epic_id=created["id"])
        assert result["status"] == "Archived"


# ── Issues ───────────────────────────────────────────────────────────


class TestIssueHandlers:

    def test_create_issue(self, db_setup):
        result = handle_issues_create(name="Bug fix", type="Bug", priority="High")
        assert result["id"].startswith("issue-")
        assert result["type"] == "Bug"

    def test_create_requires_name(self, db_setup):
        with pytest.raises(RivaError, match="name is required"):
            handle_issues_create(name="")

    def test_create_issue_with_epic(self, db_setup):
        epic = handle_epics_create(name="Parent", act_id="act-test")
        result = handle_issues_create(name="Child", epic_id=epic["id"])
        assert result["epic_id"] == epic["id"]

    def test_list_issues(self, db_setup):
        handle_issues_create(name="I1")
        handle_issues_create(name="I2")
        result = handle_issues_list()
        assert len(result["issues"]) == 2

    def test_list_issues_by_epic(self, db_setup):
        epic = handle_epics_create(name="Parent", act_id="act-test")
        handle_issues_create(name="Linked", epic_id=epic["id"])
        handle_issues_create(name="Unlinked")
        result = handle_issues_list(epic_id=epic["id"])
        assert len(result["issues"]) == 1

    def test_get_issue(self, db_setup):
        created = handle_issues_create(name="Find Me")
        result = handle_issues_get(issue_id=created["id"])
        assert result["name"] == "Find Me"

    def test_update_issue(self, db_setup):
        created = handle_issues_create(name="Original")
        result = handle_issues_update(issue_id=created["id"], status="In Progress")
        assert result["status"] == "In Progress"


# ── Cycles ───────────────────────────────────────────────────────────


class TestCycleHandlers:

    def test_create_cycle(self, db_setup):
        result = handle_cycles_create(name="Sprint 1", status="Active", goal="Ship it")
        assert result["id"].startswith("cycle-")
        assert result["goal"] == "Ship it"

    def test_create_requires_name(self, db_setup):
        with pytest.raises(RivaError, match="name is required"):
            handle_cycles_create(name="")

    def test_list_cycles(self, db_setup):
        handle_cycles_create(name="S1")
        handle_cycles_create(name="S2")
        result = handle_cycles_list()
        assert len(result["cycles"]) == 2

    def test_get_cycle(self, db_setup):
        created = handle_cycles_create(name="Find Me")
        result = handle_cycles_get(cycle_id=created["id"])
        assert result["name"] == "Find Me"

    def test_update_cycle(self, db_setup):
        created = handle_cycles_create(name="Sprint")
        result = handle_cycles_update(
            cycle_id=created["id"], retrospective="Good sprint"
        )
        assert result["retrospective"] == "Good sprint"

    def test_cycle_issue_management(self, db_setup):
        cycle = handle_cycles_create(name="Sprint 1")
        issue = handle_issues_create(name="Task 1")

        # Add
        handle_cycles_add_issue(cycle_id=cycle["id"], issue_id=issue["id"])
        result = handle_cycles_issues(cycle_id=cycle["id"])
        assert len(result["issues"]) == 1

        # Remove
        handle_cycles_remove_issue(cycle_id=cycle["id"], issue_id=issue["id"])
        result = handle_cycles_issues(cycle_id=cycle["id"])
        assert len(result["issues"]) == 0

    def test_add_issue_missing_params(self, db_setup):
        with pytest.raises(RivaError, match="required"):
            handle_cycles_add_issue(cycle_id="", issue_id="x")


# ── Roadmap ──────────────────────────────────────────────────────────


class TestRoadmapHandlers:

    def test_create_roadmap(self, db_setup):
        result = handle_roadmap_create(
            name="Q2 Launch", quarter="Q2 2026", project="CAIRN"
        )
        assert result["id"].startswith("road-")
        assert result["quarter"] == "Q2 2026"

    def test_create_requires_name(self, db_setup):
        with pytest.raises(RivaError, match="name is required"):
            handle_roadmap_create(name="")

    def test_list_roadmap(self, db_setup):
        handle_roadmap_create(name="R1", quarter="Q2 2026")
        handle_roadmap_create(name="R2", quarter="Q3 2026")
        result = handle_roadmap_list()
        assert len(result["roadmap"]) == 2

    def test_list_roadmap_filtered(self, db_setup):
        handle_roadmap_create(name="R1", quarter="Q2 2026")
        handle_roadmap_create(name="R2", quarter="Q3 2026")
        result = handle_roadmap_list(quarter="Q2 2026")
        assert len(result["roadmap"]) == 1

    def test_get_roadmap(self, db_setup):
        created = handle_roadmap_create(name="Find Me")
        result = handle_roadmap_get(roadmap_id=created["id"])
        assert result["name"] == "Find Me"

    def test_update_roadmap(self, db_setup):
        created = handle_roadmap_create(name="Original")
        result = handle_roadmap_update(roadmap_id=created["id"], status="Planned")
        assert result["status"] == "Planned"

    def test_link_unlink_epic(self, db_setup):
        road = handle_roadmap_create(name="Q2")
        epic = handle_epics_create(name="Big Feature", act_id="act-test")
        result = handle_roadmap_link_epic(
            roadmap_id=road["id"], epic_id=epic["id"]
        )
        assert result["linked"] is True

        result = handle_roadmap_unlink_epic(
            roadmap_id=road["id"], epic_id=epic["id"]
        )
        assert result["linked"] is False

    def test_link_missing_params(self, db_setup):
        with pytest.raises(RivaError, match="required"):
            handle_roadmap_link_epic(roadmap_id="", epic_id="x")


# ── Research ─────────────────────────────────────────────────────────


class TestResearchHandlers:

    def test_create_research(self, db_setup):
        result = handle_research_create(
            name="Auth decision",
            type="Architecture Decision",
            project="CAIRN",
            key_finding="PAM over Polkit",
            date="2026-03-22",
        )
        assert result["id"].startswith("res-")
        assert result["type"] == "Architecture Decision"

    def test_create_requires_name(self, db_setup):
        with pytest.raises(RivaError, match="name is required"):
            handle_research_create(name="")

    def test_list_research(self, db_setup):
        handle_research_create(name="R1", project="CAIRN")
        handle_research_create(name="R2", project="ReOS")
        result = handle_research_list()
        assert len(result["research"]) == 2

    def test_list_research_filtered(self, db_setup):
        handle_research_create(name="R1", project="CAIRN")
        handle_research_create(name="R2", project="ReOS")
        result = handle_research_list(project="CAIRN")
        assert len(result["research"]) == 1

    def test_get_research(self, db_setup):
        created = handle_research_create(name="Find Me")
        result = handle_research_get(research_id=created["id"])
        assert result["name"] == "Find Me"

    def test_update_research(self, db_setup):
        created = handle_research_create(name="Original")
        result = handle_research_update(
            research_id=created["id"], status="Complete"
        )
        assert result["status"] == "Complete"


# ── Dashboard ────────────────────────────────────────────────────────


class TestDashboardHandler:

    def test_dashboard_empty(self, db_setup):
        result = handle_dashboard()
        assert result["epics"] == {}
        assert result["issues"] == {}
        assert result["active_cycle"] is None

    def test_dashboard_with_data(self, db_setup):
        handle_epics_create(name="E1", status="Active", act_id="act-test")
        handle_issues_create(name="I1", status="In Progress")
        handle_cycles_create(name="Sprint", status="Active")
        handle_research_create(name="R1", date="2026-03-22")

        result = handle_dashboard()
        assert result["epics"]["Active"] == 1
        assert result["issues"]["In Progress"] == 1
        assert result["active_cycle"] is not None
        assert len(result["recent_research"]) == 1
