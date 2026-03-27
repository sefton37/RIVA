"""RPC handlers for the PM domain (project management).

Thin wrappers around pm_store functions. Each handler validates
required params, delegates to the store, and returns dicts.

Methods:
    riva/pm/epics/list       — List epics (filter by status, project)
    riva/pm/epics/create     — Create an epic
    riva/pm/epics/get        — Get epic details
    riva/pm/epics/update     — Update epic fields
    riva/pm/epics/archive    — Archive an epic

    riva/pm/issues/list      — List issues (filter by status, epic_id, cycle_id)
    riva/pm/issues/create    — Create an issue
    riva/pm/issues/get       — Get issue details
    riva/pm/issues/update    — Update issue fields

    riva/pm/cycles/list      — List cycles (filter by status)
    riva/pm/cycles/create    — Create a cycle
    riva/pm/cycles/get       — Get cycle details
    riva/pm/cycles/update    — Update cycle fields
    riva/pm/cycles/issues    — Get issues in a cycle
    riva/pm/cycles/add_issue — Add issue to cycle
    riva/pm/cycles/remove_issue — Remove issue from cycle

    riva/pm/roadmap/list     — List roadmap items
    riva/pm/roadmap/create   — Create a roadmap item
    riva/pm/roadmap/get      — Get roadmap item
    riva/pm/roadmap/update   — Update roadmap item
    riva/pm/roadmap/link_epic   — Link epic to roadmap item
    riva/pm/roadmap/unlink_epic — Unlink epic from roadmap item

    riva/pm/research/list    — List research (filter by project, type, epic_id)
    riva/pm/research/create  — Create a research entry
    riva/pm/research/get     — Get research entry
    riva/pm/research/update  — Update research entry

    riva/pm/dashboard        — Aggregate PM stats
"""

from __future__ import annotations

import logging
from typing import Any

from riva.errors import PmError, RivaError
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

logger = logging.getLogger(__name__)


# ── Epics ────────────────────────────────────────────────────────────


def handle_epics_create(
    *,
    name: str = "",
    act_id: str = "",
    status: str = "Backlog",
    project: str | None = None,
    priority: str = "Medium",
    target_quarter: str | None = None,
    owner: str | None = None,
    description: str | None = None,
    success_criteria: str | None = None,
    notes: str | None = None,
    **_kw,
) -> dict[str, Any]:
    if not name:
        raise RivaError("name is required")
    if not act_id:
        raise RivaError("act_id is required: every epic must be linked to a Play Act")
    epic = create_epic(
        name, act_id=act_id, status=status, project=project, priority=priority,
        target_quarter=target_quarter, owner=owner, description=description,
        success_criteria=success_criteria, notes=notes,
    )
    return epic.to_dict()


def handle_epics_list(
    *, status: str | None = None, project: str | None = None, **_kw
) -> dict[str, Any]:
    epics = list_epics(status=status, project=project)
    return {"epics": [e.to_dict() for e in epics]}


def handle_epics_get(*, epic_id: str = "", **_kw) -> dict[str, Any]:
    if not epic_id:
        raise RivaError("epic_id is required")
    epic = get_epic(epic_id)
    if epic is None:
        raise RivaError(f"Epic not found: {epic_id}")
    return epic.to_dict()


def handle_epics_update(*, epic_id: str = "", **_kw) -> dict[str, Any]:
    if not epic_id:
        raise RivaError("epic_id is required")
    fields = {k: v for k, v in _kw.items() if v is not None}
    if not fields:
        raise RivaError("No fields to update")
    epic = update_epic(epic_id, **fields)
    return epic.to_dict()


def handle_epics_archive(*, epic_id: str = "", **_kw) -> dict[str, Any]:
    if not epic_id:
        raise RivaError("epic_id is required")
    archive_epic(epic_id)
    return {"epic_id": epic_id, "status": "Archived"}


# ── Issues ───────────────────────────────────────────────────────────


def handle_issues_create(
    *,
    name: str = "",
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
    **_kw,
) -> dict[str, Any]:
    if not name:
        raise RivaError("name is required")
    issue = create_issue(
        name, status=status, priority=priority, type=type,
        epic_id=epic_id, cycle_id=cycle_id, estimate=estimate,
        assignee=assignee, forgejo_link=forgejo_link, branch=branch,
        acceptance_criteria=acceptance_criteria, notes=notes,
    )
    return issue.to_dict()


def handle_issues_list(
    *,
    status: str | None = None,
    epic_id: str | None = None,
    cycle_id: str | None = None,
    **_kw,
) -> dict[str, Any]:
    issues = list_issues(status=status, epic_id=epic_id, cycle_id=cycle_id)
    return {"issues": [i.to_dict() for i in issues]}


def handle_issues_get(*, issue_id: str = "", **_kw) -> dict[str, Any]:
    if not issue_id:
        raise RivaError("issue_id is required")
    issue = get_issue(issue_id)
    if issue is None:
        raise RivaError(f"Issue not found: {issue_id}")
    return issue.to_dict()


def handle_issues_update(*, issue_id: str = "", **_kw) -> dict[str, Any]:
    if not issue_id:
        raise RivaError("issue_id is required")
    fields = {k: v for k, v in _kw.items() if v is not None}
    if not fields:
        raise RivaError("No fields to update")
    issue = update_issue(issue_id, **fields)
    return issue.to_dict()


# ── Cycles ───────────────────────────────────────────────────────────


def handle_cycles_create(
    *,
    name: str = "",
    status: str = "Planned",
    start_date: str | None = None,
    end_date: str | None = None,
    goal: str | None = None,
    **_kw,
) -> dict[str, Any]:
    if not name:
        raise RivaError("name is required")
    cycle = create_cycle(
        name, status=status, start_date=start_date,
        end_date=end_date, goal=goal,
    )
    return cycle.to_dict()


def handle_cycles_list(*, status: str | None = None, **_kw) -> dict[str, Any]:
    cycles = list_cycles(status=status)
    return {"cycles": [c.to_dict() for c in cycles]}


def handle_cycles_get(*, cycle_id: str = "", **_kw) -> dict[str, Any]:
    if not cycle_id:
        raise RivaError("cycle_id is required")
    cycle = get_cycle(cycle_id)
    if cycle is None:
        raise RivaError(f"Cycle not found: {cycle_id}")
    return cycle.to_dict()


def handle_cycles_update(*, cycle_id: str = "", **_kw) -> dict[str, Any]:
    if not cycle_id:
        raise RivaError("cycle_id is required")
    fields = {k: v for k, v in _kw.items() if v is not None}
    if not fields:
        raise RivaError("No fields to update")
    cycle = update_cycle(cycle_id, **fields)
    return cycle.to_dict()


def handle_cycles_issues(*, cycle_id: str = "", **_kw) -> dict[str, Any]:
    if not cycle_id:
        raise RivaError("cycle_id is required")
    issues = get_cycle_issues(cycle_id)
    return {"issues": [i.to_dict() for i in issues]}


def handle_cycles_add_issue(
    *, cycle_id: str = "", issue_id: str = "", **_kw
) -> dict[str, Any]:
    if not cycle_id or not issue_id:
        raise RivaError("cycle_id and issue_id are required")
    add_issue_to_cycle(cycle_id, issue_id)
    return {"cycle_id": cycle_id, "issue_id": issue_id, "linked": True}


def handle_cycles_remove_issue(
    *, cycle_id: str = "", issue_id: str = "", **_kw
) -> dict[str, Any]:
    if not cycle_id or not issue_id:
        raise RivaError("cycle_id and issue_id are required")
    remove_issue_from_cycle(cycle_id, issue_id)
    return {"cycle_id": cycle_id, "issue_id": issue_id, "linked": False}


# ── Roadmap ──────────────────────────────────────────────────────────


def handle_roadmap_create(
    *,
    name: str = "",
    status: str = "Idea",
    quarter: str | None = None,
    project: str | None = None,
    description: str | None = None,
    why: str | None = None,
    dependencies: str | None = None,
    **_kw,
) -> dict[str, Any]:
    if not name:
        raise RivaError("name is required")
    item = create_roadmap_item(
        name, status=status, quarter=quarter, project=project,
        description=description, why=why, dependencies=dependencies,
    )
    return item.to_dict()


def handle_roadmap_list(
    *, quarter: str | None = None, project: str | None = None, **_kw
) -> dict[str, Any]:
    items = list_roadmap(quarter=quarter, project=project)
    return {"roadmap": [i.to_dict() for i in items]}


def handle_roadmap_get(*, roadmap_id: str = "", **_kw) -> dict[str, Any]:
    if not roadmap_id:
        raise RivaError("roadmap_id is required")
    item = get_roadmap_item(roadmap_id)
    if item is None:
        raise RivaError(f"Roadmap item not found: {roadmap_id}")
    return item.to_dict()


def handle_roadmap_update(*, roadmap_id: str = "", **_kw) -> dict[str, Any]:
    if not roadmap_id:
        raise RivaError("roadmap_id is required")
    fields = {k: v for k, v in _kw.items() if v is not None}
    if not fields:
        raise RivaError("No fields to update")
    item = update_roadmap_item(roadmap_id, **fields)
    return item.to_dict()


def handle_roadmap_link_epic(
    *, roadmap_id: str = "", epic_id: str = "", **_kw
) -> dict[str, Any]:
    if not roadmap_id or not epic_id:
        raise RivaError("roadmap_id and epic_id are required")
    link_epic_to_roadmap(roadmap_id, epic_id)
    return {"roadmap_id": roadmap_id, "epic_id": epic_id, "linked": True}


def handle_roadmap_unlink_epic(
    *, roadmap_id: str = "", epic_id: str = "", **_kw
) -> dict[str, Any]:
    if not roadmap_id or not epic_id:
        raise RivaError("roadmap_id and epic_id are required")
    unlink_epic_from_roadmap(roadmap_id, epic_id)
    return {"roadmap_id": roadmap_id, "epic_id": epic_id, "linked": False}


# ── Research ─────────────────────────────────────────────────────────


def handle_research_create(
    *,
    name: str = "",
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
    **_kw,
) -> dict[str, Any]:
    if not name:
        raise RivaError("name is required")
    res = create_research(
        name, type=type, status=status, project=project,
        epic_id=epic_id, issue_id=issue_id, source=source,
        key_finding=key_finding, date=date, tags=tags, doc_path=doc_path,
    )
    return res.to_dict()


def handle_research_list(
    *,
    project: str | None = None,
    type: str | None = None,
    epic_id: str | None = None,
    **_kw,
) -> dict[str, Any]:
    entries = list_research(project=project, type=type, epic_id=epic_id)
    return {"research": [r.to_dict() for r in entries]}


def handle_research_get(*, research_id: str = "", **_kw) -> dict[str, Any]:
    if not research_id:
        raise RivaError("research_id is required")
    res = get_research(research_id)
    if res is None:
        raise RivaError(f"Research not found: {research_id}")
    return res.to_dict()


def handle_research_update(*, research_id: str = "", **_kw) -> dict[str, Any]:
    if not research_id:
        raise RivaError("research_id is required")
    fields = {k: v for k, v in _kw.items() if v is not None}
    if not fields:
        raise RivaError("No fields to update")
    res = update_research(research_id, **fields)
    return res.to_dict()


# ── Dashboard ────────────────────────────────────────────────────────


def handle_dashboard(**_kw) -> dict[str, Any]:
    return get_dashboard()
