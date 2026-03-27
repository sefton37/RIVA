"""PM Store: CRUD operations for project management tables.

Manages pm_epics, pm_issues, pm_cycles, pm_roadmap, pm_research
in the shared talkingrock.db. Follows the same patterns as
contract_store.py and properties_store.py.

All update_* functions use column allowlists to prevent injection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from riva.db import get_connection, transaction
from riva.errors import PmError
from riva.models import PmCycle, PmEpic, PmIssue, PmResearch, PmRoadmapItem

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Column allowlists for dynamic update SQL ─────────────────────────

_EPIC_FIELDS = {
    "name", "status", "project", "priority", "target_quarter",
    "owner", "description", "success_criteria", "notes", "act_id",
}

_CYCLE_FIELDS = {
    "name", "status", "start_date", "end_date", "goal", "retrospective",
}

_ISSUE_FIELDS = {
    "name", "status", "priority", "type", "epic_id", "cycle_id",
    "estimate", "assignee", "forgejo_link", "branch",
    "acceptance_criteria", "notes",
}

_ROADMAP_FIELDS = {
    "name", "status", "quarter", "project", "description", "why", "dependencies",
}

_RESEARCH_FIELDS = {
    "name", "type", "status", "project", "epic_id", "issue_id",
    "source", "key_finding", "date", "tags", "doc_path",
}


_ALLOWED_TABLES = frozenset({
    "pm_epics", "pm_cycles", "pm_issues", "pm_roadmap", "pm_research",
})


def _build_update(
    table: str, row_id: str, allowed: set[str], fields: dict[str, Any]
) -> tuple[str, list[Any]]:
    """Build a parameterised UPDATE statement from caller-supplied fields.

    Raises PmError for unknown field names or table names.
    """
    if table not in _ALLOWED_TABLES:
        raise PmError(f"Unknown table: {table}")
    updates: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise PmError(f"Unknown field for {table}: {key}")
        updates.append(f"{key}=?")
        params.append(value)

    if not updates:
        raise PmError("No fields to update")

    updates.append("updated_at=?")
    params.append(_now())
    params.append(row_id)

    sql = f"UPDATE {table} SET {', '.join(updates)} WHERE id=?"  # noqa: S608
    return sql, params


# ── Epics ────────────────────────────────────────────────────────────


def create_epic(
    name: str,
    *,
    act_id: str,
    status: str = "Backlog",
    project: str | None = None,
    priority: str = "Medium",
    target_quarter: str | None = None,
    owner: str | None = None,
    description: str | None = None,
    success_criteria: str | None = None,
    notes: str | None = None,
) -> PmEpic:
    """Create an epic. act_id is required — every epic belongs to a Play Act."""
    if not act_id:
        raise PmError("act_id is required: every epic must be linked to a Play Act")
    epic_id = f"epic-{uuid4().hex[:12]}"
    now = _now()

    with transaction() as conn:
        conn.execute(
            "INSERT INTO pm_epics "
            "(id, name, status, project, priority, target_quarter, owner, "
            "description, success_criteria, notes, act_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (epic_id, name, status, project, priority, target_quarter, owner,
             description, success_criteria, notes, act_id, now, now),
        )

    logger.info("Created epic %s: %s", epic_id, name)
    return PmEpic(
        id=epic_id, name=name, status=status, project=project,
        priority=priority, target_quarter=target_quarter, owner=owner,
        description=description, success_criteria=success_criteria,
        notes=notes, act_id=act_id, created_at=now, updated_at=now,
    )


def get_epic(epic_id: str) -> PmEpic | None:
    conn = get_connection(readonly=True)
    try:
        row = conn.execute("SELECT * FROM pm_epics WHERE id=?", (epic_id,)).fetchone()
        return PmEpic.from_row(row) if row else None
    finally:
        conn.close()


def list_epics(
    *, status: str | None = None, project: str | None = None
) -> list[PmEpic]:
    conn = get_connection(readonly=True)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if project:
            clauses.append("project=?")
            params.append(project)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM pm_epics{where} ORDER BY "  # noqa: S608
            "CASE priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 "
            "WHEN 'Medium' THEN 3 WHEN 'Low' THEN 4 END, created_at DESC",
            params,
        ).fetchall()
        return [PmEpic.from_row(r) for r in rows]
    finally:
        conn.close()


def update_epic(epic_id: str, **fields: Any) -> PmEpic:
    sql, params = _build_update("pm_epics", epic_id, _EPIC_FIELDS, fields)
    with transaction() as conn:
        result = conn.execute(sql, params)
        if result.rowcount == 0:
            raise PmError(f"Epic not found: {epic_id}")

    epic = get_epic(epic_id)
    if epic is None:
        raise PmError(f"Epic disappeared after update: {epic_id}")
    return epic


def archive_epic(epic_id: str) -> None:
    now = _now()
    with transaction() as conn:
        result = conn.execute(
            "UPDATE pm_epics SET status='Archived', updated_at=? WHERE id=?",
            (now, epic_id),
        )
        if result.rowcount == 0:
            raise PmError(f"Epic not found: {epic_id}")
    logger.info("Archived epic %s", epic_id)


# ── Cycles ───────────────────────────────────────────────────────────


def create_cycle(
    name: str,
    *,
    status: str = "Planned",
    start_date: str | None = None,
    end_date: str | None = None,
    goal: str | None = None,
) -> PmCycle:
    cycle_id = f"cycle-{uuid4().hex[:12]}"
    now = _now()

    with transaction() as conn:
        conn.execute(
            "INSERT INTO pm_cycles "
            "(id, name, status, start_date, end_date, goal, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cycle_id, name, status, start_date, end_date, goal, now, now),
        )

    logger.info("Created cycle %s: %s", cycle_id, name)
    return PmCycle(
        id=cycle_id, name=name, status=status, start_date=start_date,
        end_date=end_date, goal=goal, created_at=now, updated_at=now,
    )


def get_cycle(cycle_id: str) -> PmCycle | None:
    conn = get_connection(readonly=True)
    try:
        row = conn.execute("SELECT * FROM pm_cycles WHERE id=?", (cycle_id,)).fetchone()
        return PmCycle.from_row(row) if row else None
    finally:
        conn.close()


def list_cycles(*, status: str | None = None) -> list[PmCycle]:
    conn = get_connection(readonly=True)
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM pm_cycles WHERE status=? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pm_cycles ORDER BY created_at DESC"
            ).fetchall()
        return [PmCycle.from_row(r) for r in rows]
    finally:
        conn.close()


def update_cycle(cycle_id: str, **fields: Any) -> PmCycle:
    sql, params = _build_update("pm_cycles", cycle_id, _CYCLE_FIELDS, fields)
    with transaction() as conn:
        result = conn.execute(sql, params)
        if result.rowcount == 0:
            raise PmError(f"Cycle not found: {cycle_id}")

    cycle = get_cycle(cycle_id)
    if cycle is None:
        raise PmError(f"Cycle disappeared after update: {cycle_id}")
    return cycle


def add_issue_to_cycle(cycle_id: str, issue_id: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO pm_cycle_issues (cycle_id, issue_id) VALUES (?, ?)",
            (cycle_id, issue_id),
        )


def remove_issue_from_cycle(cycle_id: str, issue_id: str) -> None:
    with transaction() as conn:
        result = conn.execute(
            "DELETE FROM pm_cycle_issues WHERE cycle_id=? AND issue_id=?",
            (cycle_id, issue_id),
        )
        if result.rowcount == 0:
            raise PmError(f"Cycle-issue link not found: {cycle_id}/{issue_id}")


def get_cycle_issues(cycle_id: str) -> list[PmIssue]:
    conn = get_connection(readonly=True)
    try:
        rows = conn.execute(
            "SELECT i.* FROM pm_issues i "
            "JOIN pm_cycle_issues ci ON i.id = ci.issue_id "
            "WHERE ci.cycle_id=? ORDER BY "
            "CASE i.priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 "
            "WHEN 'Medium' THEN 3 WHEN 'Low' THEN 4 END",
            (cycle_id,),
        ).fetchall()
        return [PmIssue.from_row(r) for r in rows]
    finally:
        conn.close()


# ── Issues ───────────────────────────────────────────────────────────


def create_issue(
    name: str,
    *,
    status: str = "Backlog",
    priority: str = "Medium",
    type: str = "Feature",
    epic_id: str | None = None,
    cycle_id: str | None = None,
    estimate: str | None = None,
    assignee: str | None = None,
    forgejo_link: str | None = None,
    branch: str | None = None,
    acceptance_criteria: str | None = None,
    notes: str | None = None,
) -> PmIssue:
    issue_id = f"issue-{uuid4().hex[:12]}"
    now = _now()

    with transaction() as conn:
        conn.execute(
            "INSERT INTO pm_issues "
            "(id, name, status, priority, type, epic_id, cycle_id, estimate, "
            "assignee, forgejo_link, branch, acceptance_criteria, notes, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (issue_id, name, status, priority, type, epic_id, cycle_id,
             estimate, assignee, forgejo_link, branch, acceptance_criteria,
             notes, now, now),
        )

    logger.info("Created issue %s: %s", issue_id, name)
    return PmIssue(
        id=issue_id, name=name, status=status, priority=priority,
        type=type, epic_id=epic_id, cycle_id=cycle_id, estimate=estimate,
        assignee=assignee, forgejo_link=forgejo_link, branch=branch,
        acceptance_criteria=acceptance_criteria, notes=notes,
        created_at=now, updated_at=now,
    )


def get_issue(issue_id: str) -> PmIssue | None:
    conn = get_connection(readonly=True)
    try:
        row = conn.execute("SELECT * FROM pm_issues WHERE id=?", (issue_id,)).fetchone()
        return PmIssue.from_row(row) if row else None
    finally:
        conn.close()


def list_issues(
    *,
    status: str | None = None,
    epic_id: str | None = None,
    cycle_id: str | None = None,
) -> list[PmIssue]:
    conn = get_connection(readonly=True)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if epic_id:
            clauses.append("epic_id=?")
            params.append(epic_id)
        if cycle_id:
            clauses.append(
                "id IN (SELECT issue_id FROM pm_cycle_issues WHERE cycle_id=?)"
            )
            params.append(cycle_id)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM pm_issues{where} ORDER BY "  # noqa: S608
            "CASE priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 "
            "WHEN 'Medium' THEN 3 WHEN 'Low' THEN 4 END, created_at DESC",
            params,
        ).fetchall()
        return [PmIssue.from_row(r) for r in rows]
    finally:
        conn.close()


def update_issue(issue_id: str, **fields: Any) -> PmIssue:
    sql, params = _build_update("pm_issues", issue_id, _ISSUE_FIELDS, fields)
    with transaction() as conn:
        result = conn.execute(sql, params)
        if result.rowcount == 0:
            raise PmError(f"Issue not found: {issue_id}")

    issue = get_issue(issue_id)
    if issue is None:
        raise PmError(f"Issue disappeared after update: {issue_id}")
    return issue


# ── Roadmap ──────────────────────────────────────────────────────────


def create_roadmap_item(
    name: str,
    *,
    status: str = "Idea",
    quarter: str | None = None,
    project: str | None = None,
    description: str | None = None,
    why: str | None = None,
    dependencies: str | None = None,
) -> PmRoadmapItem:
    road_id = f"road-{uuid4().hex[:12]}"
    now = _now()

    with transaction() as conn:
        conn.execute(
            "INSERT INTO pm_roadmap "
            "(id, name, status, quarter, project, description, why, "
            "dependencies, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (road_id, name, status, quarter, project, description, why,
             dependencies, now, now),
        )

    logger.info("Created roadmap item %s: %s", road_id, name)
    return PmRoadmapItem(
        id=road_id, name=name, status=status, quarter=quarter,
        project=project, description=description, why=why,
        dependencies=dependencies, created_at=now, updated_at=now,
    )


def get_roadmap_item(roadmap_id: str) -> PmRoadmapItem | None:
    conn = get_connection(readonly=True)
    try:
        row = conn.execute(
            "SELECT * FROM pm_roadmap WHERE id=?", (roadmap_id,)
        ).fetchone()
        return PmRoadmapItem.from_row(row) if row else None
    finally:
        conn.close()


def list_roadmap(
    *, quarter: str | None = None, project: str | None = None
) -> list[PmRoadmapItem]:
    conn = get_connection(readonly=True)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if quarter:
            clauses.append("quarter=?")
            params.append(quarter)
        if project:
            clauses.append("project=?")
            params.append(project)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM pm_roadmap{where} ORDER BY quarter, created_at DESC",  # noqa: S608
            params,
        ).fetchall()
        return [PmRoadmapItem.from_row(r) for r in rows]
    finally:
        conn.close()


def update_roadmap_item(roadmap_id: str, **fields: Any) -> PmRoadmapItem:
    sql, params = _build_update("pm_roadmap", roadmap_id, _ROADMAP_FIELDS, fields)
    with transaction() as conn:
        result = conn.execute(sql, params)
        if result.rowcount == 0:
            raise PmError(f"Roadmap item not found: {roadmap_id}")

    item = get_roadmap_item(roadmap_id)
    if item is None:
        raise PmError(f"Roadmap item disappeared after update: {roadmap_id}")
    return item


def link_epic_to_roadmap(roadmap_id: str, epic_id: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO pm_roadmap_epics (roadmap_id, epic_id) VALUES (?, ?)",
            (roadmap_id, epic_id),
        )


def unlink_epic_from_roadmap(roadmap_id: str, epic_id: str) -> None:
    with transaction() as conn:
        result = conn.execute(
            "DELETE FROM pm_roadmap_epics WHERE roadmap_id=? AND epic_id=?",
            (roadmap_id, epic_id),
        )
        if result.rowcount == 0:
            raise PmError(f"Roadmap-epic link not found: {roadmap_id}/{epic_id}")


# ── Research ─────────────────────────────────────────────────────────


def create_research(
    name: str,
    *,
    type: str | None = None,
    status: str = "In Progress",
    project: str | None = None,
    epic_id: str | None = None,
    issue_id: str | None = None,
    source: str | None = None,
    key_finding: str | None = None,
    date: str | None = None,
    tags: str | None = None,
    doc_path: str | None = None,
) -> PmResearch:
    res_id = f"res-{uuid4().hex[:12]}"
    now = _now()

    with transaction() as conn:
        conn.execute(
            "INSERT INTO pm_research "
            "(id, name, type, status, project, epic_id, issue_id, source, "
            "key_finding, date, tags, doc_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (res_id, name, type, status, project, epic_id, issue_id,
             source, key_finding, date, tags, doc_path, now, now),
        )

    logger.info("Created research %s: %s", res_id, name)
    return PmResearch(
        id=res_id, name=name, type=type, status=status, project=project,
        epic_id=epic_id, issue_id=issue_id, source=source,
        key_finding=key_finding, date=date, tags=tags, doc_path=doc_path,
        created_at=now, updated_at=now,
    )


def get_research(research_id: str) -> PmResearch | None:
    conn = get_connection(readonly=True)
    try:
        row = conn.execute(
            "SELECT * FROM pm_research WHERE id=?", (research_id,)
        ).fetchone()
        return PmResearch.from_row(row) if row else None
    finally:
        conn.close()


def list_research(
    *,
    project: str | None = None,
    type: str | None = None,
    epic_id: str | None = None,
) -> list[PmResearch]:
    conn = get_connection(readonly=True)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if project:
            clauses.append("project=?")
            params.append(project)
        if type:
            clauses.append("type=?")
            params.append(type)
        if epic_id:
            clauses.append("epic_id=?")
            params.append(epic_id)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM pm_research{where} ORDER BY date DESC, "  # noqa: S608
            "created_at DESC",
            params,
        ).fetchall()
        return [PmResearch.from_row(r) for r in rows]
    finally:
        conn.close()


def update_research(research_id: str, **fields: Any) -> PmResearch:
    sql, params = _build_update("pm_research", research_id, _RESEARCH_FIELDS, fields)
    with transaction() as conn:
        result = conn.execute(sql, params)
        if result.rowcount == 0:
            raise PmError(f"Research not found: {research_id}")

    res = get_research(research_id)
    if res is None:
        raise PmError(f"Research disappeared after update: {research_id}")
    return res


# ── Dashboard ────────────────────────────────────────────────────────


def get_dashboard() -> dict[str, Any]:
    """Aggregate PM stats for the RIVA UI dashboard."""
    conn = get_connection(readonly=True)
    try:
        epic_counts = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) as cnt FROM pm_epics GROUP BY status"
        ).fetchall():
            epic_counts[row["status"]] = row["cnt"]

        issue_counts = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) as cnt FROM pm_issues GROUP BY status"
        ).fetchall():
            issue_counts[row["status"]] = row["cnt"]

        active_cycle = conn.execute(
            "SELECT * FROM pm_cycles WHERE status='Active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        recent_research = conn.execute(
            "SELECT id, name, type, project, date FROM pm_research "
            "ORDER BY date DESC LIMIT 5"
        ).fetchall()

        return {
            "epics": epic_counts,
            "issues": issue_counts,
            "active_cycle": dict(active_cycle) if active_cycle else None,
            "recent_research": [dict(r) for r in recent_research],
        }
    finally:
        conn.close()
