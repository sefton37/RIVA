"""Tests for the PM store CRUD operations."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from riva.errors import PmError
from riva.pm_store import (
    add_issue_to_cycle,
    archive_epic,
    create_cycle,
    create_epic,
    create_issue,
    create_research,
    create_roadmap_item,
    get_cycle,
    get_cycle_issues,
    get_dashboard,
    get_epic,
    get_issue,
    get_research,
    get_roadmap_item,
    link_epic_to_roadmap,
    list_cycles,
    list_epics,
    list_issues,
    list_research,
    list_roadmap,
    remove_issue_from_cycle,
    unlink_epic_from_roadmap,
    update_cycle,
    update_epic,
    update_issue,
    update_research,
    update_roadmap_item,
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


class TestEpicStore:

    def test_create_epic(self, db_setup):
        epic = create_epic("Test Epic", project="TestProject", priority="High", act_id="act-test")
        assert epic.id.startswith("epic-")
        assert epic.name == "Test Epic"
        assert epic.project == "TestProject"
        assert epic.priority == "High"
        assert epic.status == "Backlog"
        assert epic.created_at != ""
        assert epic.updated_at != ""

    def test_get_epic(self, db_setup):
        created = create_epic("Find Me", act_id="act-test")
        found = get_epic(created.id)
        assert found is not None
        assert found.name == "Find Me"

    def test_get_epic_not_found(self, db_setup):
        assert get_epic("epic-nonexistent") is None

    def test_list_epics_no_filter(self, db_setup):
        create_epic("A", priority="Low", act_id="act-test")
        create_epic("B", priority="High", act_id="act-test")
        epics = list_epics()
        assert len(epics) == 2
        # High should come first
        assert epics[0].priority == "High"

    def test_list_epics_by_status(self, db_setup):
        create_epic("Active One", status="Active", act_id="act-test")
        create_epic("Backlog One", act_id="act-test")
        epics = list_epics(status="Active")
        assert len(epics) == 1
        assert epics[0].name == "Active One"

    def test_list_epics_by_project(self, db_setup):
        create_epic("Cairn Epic", project="CAIRN", act_id="act-test")
        create_epic("ReOS Epic", project="ReOS", act_id="act-test")
        epics = list_epics(project="CAIRN")
        assert len(epics) == 1
        assert epics[0].project == "CAIRN"

    def test_update_epic(self, db_setup):
        epic = create_epic("Original", act_id="act-test")
        updated = update_epic(epic.id, name="Updated", priority="Critical")
        assert updated.name == "Updated"
        assert updated.priority == "Critical"
        assert updated.updated_at >= epic.updated_at

    def test_update_epic_not_found(self, db_setup):
        with pytest.raises(PmError, match="Epic not found"):
            update_epic("epic-nonexistent", name="X")

    def test_update_epic_unknown_field(self, db_setup):
        epic = create_epic("Test", act_id="act-test")
        with pytest.raises(PmError, match="Unknown field"):
            update_epic(epic.id, bad_field="X")

    def test_update_epic_no_fields(self, db_setup):
        epic = create_epic("Test", act_id="act-test")
        with pytest.raises(PmError, match="No fields to update"):
            update_epic(epic.id)

    def test_archive_epic(self, db_setup):
        epic = create_epic("To Archive", act_id="act-test")
        archive_epic(epic.id)
        found = get_epic(epic.id)
        assert found is not None
        assert found.status == "Archived"

    def test_archive_epic_not_found(self, db_setup):
        with pytest.raises(PmError, match="Epic not found"):
            archive_epic("epic-nonexistent")

    def test_create_epic_requires_act_id(self, db_setup):
        with pytest.raises(PmError, match="act_id is required"):
            create_epic("No Act", act_id="")

    def test_create_epic_with_act_id(self, db_setup):
        epic = create_epic("Linked", act_id="act-abc123")
        assert epic.act_id == "act-abc123"
        found = get_epic(epic.id)
        assert found is not None
        assert found.act_id == "act-abc123"


# ── Cycles ───────────────────────────────────────────────────────────


class TestCycleStore:

    def test_create_cycle(self, db_setup):
        cycle = create_cycle("Sprint 1", status="Active", goal="Ship it")
        assert cycle.id.startswith("cycle-")
        assert cycle.name == "Sprint 1"
        assert cycle.status == "Active"
        assert cycle.goal == "Ship it"

    def test_get_cycle(self, db_setup):
        created = create_cycle("Find Me")
        found = get_cycle(created.id)
        assert found is not None
        assert found.name == "Find Me"

    def test_get_cycle_not_found(self, db_setup):
        assert get_cycle("cycle-nonexistent") is None

    def test_list_cycles(self, db_setup):
        create_cycle("Active Sprint", status="Active")
        create_cycle("Planned Sprint")
        cycles = list_cycles()
        assert len(cycles) == 2

    def test_list_cycles_by_status(self, db_setup):
        create_cycle("Active Sprint", status="Active")
        create_cycle("Planned Sprint")
        cycles = list_cycles(status="Active")
        assert len(cycles) == 1

    def test_update_cycle(self, db_setup):
        cycle = create_cycle("Sprint")
        updated = update_cycle(cycle.id, status="Complete", retrospective="Good sprint")
        assert updated.status == "Complete"
        assert updated.retrospective == "Good sprint"

    def test_update_cycle_not_found(self, db_setup):
        with pytest.raises(PmError, match="Cycle not found"):
            update_cycle("cycle-nonexistent", status="Done")

    def test_add_and_get_cycle_issues(self, db_setup):
        cycle = create_cycle("Sprint 1", status="Active")
        issue = create_issue("Task 1")
        add_issue_to_cycle(cycle.id, issue.id)
        issues = get_cycle_issues(cycle.id)
        assert len(issues) == 1
        assert issues[0].id == issue.id

    def test_remove_issue_from_cycle(self, db_setup):
        cycle = create_cycle("Sprint 1")
        issue = create_issue("Task 1")
        add_issue_to_cycle(cycle.id, issue.id)
        remove_issue_from_cycle(cycle.id, issue.id)
        issues = get_cycle_issues(cycle.id)
        assert len(issues) == 0

    def test_remove_issue_not_found(self, db_setup):
        with pytest.raises(PmError, match="Cycle-issue link not found"):
            remove_issue_from_cycle("cycle-x", "issue-x")


# ── Issues ───────────────────────────────────────────────────────────


class TestIssueStore:

    def test_create_issue(self, db_setup):
        issue = create_issue("Bug fix", type="Bug", priority="High")
        assert issue.id.startswith("issue-")
        assert issue.name == "Bug fix"
        assert issue.type == "Bug"
        assert issue.priority == "High"

    def test_create_issue_with_epic(self, db_setup):
        epic = create_epic("Parent Epic", act_id="act-test")
        issue = create_issue("Child Task", epic_id=epic.id)
        assert issue.epic_id == epic.id

    def test_get_issue(self, db_setup):
        created = create_issue("Find Me")
        found = get_issue(created.id)
        assert found is not None
        assert found.name == "Find Me"

    def test_get_issue_not_found(self, db_setup):
        assert get_issue("issue-nonexistent") is None

    def test_list_issues_no_filter(self, db_setup):
        create_issue("A", priority="Low")
        create_issue("B", priority="High")
        issues = list_issues()
        assert len(issues) == 2
        assert issues[0].priority == "High"

    def test_list_issues_by_status(self, db_setup):
        create_issue("Done One", status="Done")
        create_issue("Backlog One")
        issues = list_issues(status="Done")
        assert len(issues) == 1

    def test_list_issues_by_epic(self, db_setup):
        epic = create_epic("Parent", act_id="act-test")
        create_issue("Linked", epic_id=epic.id)
        create_issue("Unlinked")
        issues = list_issues(epic_id=epic.id)
        assert len(issues) == 1
        assert issues[0].name == "Linked"

    def test_list_issues_by_cycle(self, db_setup):
        cycle = create_cycle("Sprint")
        issue = create_issue("In Sprint")
        create_issue("Not In Sprint")
        add_issue_to_cycle(cycle.id, issue.id)
        issues = list_issues(cycle_id=cycle.id)
        assert len(issues) == 1
        assert issues[0].name == "In Sprint"

    def test_update_issue(self, db_setup):
        issue = create_issue("Original")
        updated = update_issue(issue.id, status="In Progress", assignee="kellogg")
        assert updated.status == "In Progress"
        assert updated.assignee == "kellogg"

    def test_update_issue_not_found(self, db_setup):
        with pytest.raises(PmError, match="Issue not found"):
            update_issue("issue-nonexistent", status="Done")

    def test_update_issue_unknown_field(self, db_setup):
        issue = create_issue("Test")
        with pytest.raises(PmError, match="Unknown field"):
            update_issue(issue.id, bad_field="X")


# ── Roadmap ──────────────────────────────────────────────────────────


class TestRoadmapStore:

    def test_create_roadmap_item(self, db_setup):
        item = create_roadmap_item("Q2 Launch", quarter="Q2 2026", project="CAIRN")
        assert item.id.startswith("road-")
        assert item.quarter == "Q2 2026"
        assert item.project == "CAIRN"

    def test_get_roadmap_item(self, db_setup):
        created = create_roadmap_item("Find Me")
        found = get_roadmap_item(created.id)
        assert found is not None
        assert found.name == "Find Me"

    def test_get_roadmap_not_found(self, db_setup):
        assert get_roadmap_item("road-nonexistent") is None

    def test_list_roadmap(self, db_setup):
        create_roadmap_item("A", quarter="Q2 2026")
        create_roadmap_item("B", quarter="Q3 2026")
        items = list_roadmap()
        assert len(items) == 2

    def test_list_roadmap_by_quarter(self, db_setup):
        create_roadmap_item("A", quarter="Q2 2026")
        create_roadmap_item("B", quarter="Q3 2026")
        items = list_roadmap(quarter="Q2 2026")
        assert len(items) == 1

    def test_list_roadmap_by_project(self, db_setup):
        create_roadmap_item("A", project="CAIRN")
        create_roadmap_item("B", project="ReOS")
        items = list_roadmap(project="CAIRN")
        assert len(items) == 1

    def test_update_roadmap_item(self, db_setup):
        item = create_roadmap_item("Original")
        updated = update_roadmap_item(item.id, status="Planned", why="Strategic")
        assert updated.status == "Planned"
        assert updated.why == "Strategic"

    def test_update_roadmap_not_found(self, db_setup):
        with pytest.raises(PmError, match="Roadmap item not found"):
            update_roadmap_item("road-nonexistent", status="Done")

    def test_update_roadmap_unknown_field(self, db_setup):
        item = create_roadmap_item("Test")
        with pytest.raises(PmError, match="Unknown field"):
            update_roadmap_item(item.id, bad_field="X")

    def test_link_epic_to_roadmap(self, db_setup):
        item = create_roadmap_item("Q2")
        epic = create_epic("Big Feature", act_id="act-test")
        link_epic_to_roadmap(item.id, epic.id)
        # Verify: linking twice should raise (PK constraint)
        with pytest.raises(sqlite3.IntegrityError):
            link_epic_to_roadmap(item.id, epic.id)

    def test_unlink_epic_from_roadmap(self, db_setup):
        item = create_roadmap_item("Q2")
        epic = create_epic("Feature", act_id="act-test")
        link_epic_to_roadmap(item.id, epic.id)
        unlink_epic_from_roadmap(item.id, epic.id)
        # Unlinking again should raise
        with pytest.raises(PmError, match="Roadmap-epic link not found"):
            unlink_epic_from_roadmap(item.id, epic.id)

    def test_unlink_not_found(self, db_setup):
        with pytest.raises(PmError, match="Roadmap-epic link not found"):
            unlink_epic_from_roadmap("road-x", "epic-x")


# ── Research ─────────────────────────────────────────────────────────


class TestResearchStore:

    def test_create_research(self, db_setup):
        res = create_research(
            "Auth decision",
            type="Architecture Decision",
            project="CAIRN",
            key_finding="PAM over Polkit",
            date="2026-03-22",
        )
        assert res.id.startswith("res-")
        assert res.type == "Architecture Decision"
        assert res.key_finding == "PAM over Polkit"

    def test_get_research(self, db_setup):
        created = create_research("Find Me")
        found = get_research(created.id)
        assert found is not None
        assert found.name == "Find Me"

    def test_get_research_not_found(self, db_setup):
        assert get_research("res-nonexistent") is None

    def test_list_research(self, db_setup):
        create_research("A", project="CAIRN", date="2026-03-22")
        create_research("B", project="ReOS", date="2026-03-21")
        entries = list_research()
        assert len(entries) == 2
        # Most recent date first
        assert entries[0].date == "2026-03-22"

    def test_list_research_by_project(self, db_setup):
        create_research("A", project="CAIRN")
        create_research("B", project="ReOS")
        entries = list_research(project="CAIRN")
        assert len(entries) == 1

    def test_list_research_by_type(self, db_setup):
        create_research("A", type="Architecture Decision")
        create_research("B", type="Technical Spike")
        entries = list_research(type="Technical Spike")
        assert len(entries) == 1

    def test_list_research_by_epic(self, db_setup):
        epic = create_epic("Parent", act_id="act-test")
        create_research("Linked", epic_id=epic.id)
        create_research("Unlinked")
        entries = list_research(epic_id=epic.id)
        assert len(entries) == 1

    def test_update_research(self, db_setup):
        res = create_research("Original")
        updated = update_research(res.id, status="Complete", key_finding="Found it")
        assert updated.status == "Complete"
        assert updated.key_finding == "Found it"

    def test_update_research_not_found(self, db_setup):
        with pytest.raises(PmError, match="Research not found"):
            update_research("res-nonexistent", status="Done")


# ── Dashboard ────────────────────────────────────────────────────────


class TestDashboard:

    def test_dashboard_empty(self, db_setup):
        dash = get_dashboard()
        assert dash["epics"] == {}
        assert dash["issues"] == {}
        assert dash["active_cycle"] is None
        assert dash["recent_research"] == []

    def test_dashboard_with_data(self, db_setup):
        create_epic("E1", status="Active", act_id="act-test")
        create_epic("E2", status="Backlog", act_id="act-test")
        create_issue("I1", status="In Progress")
        create_issue("I2", status="Done")
        create_cycle("Sprint", status="Active", goal="Ship")
        create_research("R1", project="CAIRN", date="2026-03-22")

        dash = get_dashboard()
        assert dash["epics"]["Active"] == 1
        assert dash["epics"]["Backlog"] == 1
        assert dash["issues"]["In Progress"] == 1
        assert dash["issues"]["Done"] == 1
        assert dash["active_cycle"] is not None
        assert dash["active_cycle"]["goal"] == "Ship"
        assert len(dash["recent_research"]) == 1
