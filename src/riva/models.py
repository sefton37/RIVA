"""RIVA data models.

Dataclasses for plans, steps, contracts, verification criteria,
and project management entities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    """A single step in a RIVA plan."""

    id: str
    plan_id: str
    step_number: int
    title: str
    description: str = ""
    acceptance_criterion: str = ""
    estimated_minutes: int | None = None
    status: str = "pending"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RivaPlan:
    """A structured work plan decomposed from a user request."""

    id: str
    project_id: str
    title: str
    user_request: str
    steps: list[PlanStep] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    estimated_minutes: int | None = None
    agent_id: str | None = None
    status: str = "draft"
    decomposition_json: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict for RPC responses."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "user_request": self.user_request,
            "agent_id": self.agent_id,
            "status": self.status,
            "estimated_minutes": self.estimated_minutes,
            "risks": self.risks,
            "steps": [
                {
                    "id": s.id,
                    "step_number": s.step_number,
                    "title": s.title,
                    "description": s.description,
                    "acceptance_criterion": s.acceptance_criterion,
                    "estimated_minutes": s.estimated_minutes,
                    "status": s.status,
                }
                for s in self.steps
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class VerificationCriterion:
    """A typed verification criterion derived from a plan step.

    Types:
        file_exists — path must exist in agent workspace
        function_defined — function name must be grep-able in file
        git_contains_change — path must appear in git diff
        git_commit_message — keyword must appear in git log
        manual_verification — always inconclusive, flagged for user
    """

    type: str
    path: str | None = None
    file: str | None = None
    name: str | None = None
    keyword: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.path is not None:
            d["path"] = self.path
        if self.file is not None:
            d["file"] = self.file
        if self.name is not None:
            d["name"] = self.name
        if self.keyword is not None:
            d["keyword"] = self.keyword
        if self.description is not None:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VerificationCriterion:
        return cls(
            type=d["type"],
            path=d.get("path"),
            file=d.get("file"),
            name=d.get("name"),
            keyword=d.get("keyword"),
            description=d.get("description"),
        )


@dataclass
class RivaContract:
    """An enforceable contract created from an approved plan."""

    id: str
    plan_id: str
    agent_id: str
    verification_criteria: list[VerificationCriterion] = field(default_factory=list)
    nol_assembly: str | None = None
    nol_intent_hash: str | None = None
    nol_verified: bool = False
    approved_at: str = ""
    approved_by: str = "user"
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "agent_id": self.agent_id,
            "verification_criteria": [c.to_dict() for c in self.verification_criteria],
            "nol_assembly": self.nol_assembly,
            "nol_intent_hash": self.nol_intent_hash,
            "nol_verified": self.nol_verified,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── Project Management Dataclasses ──────────────────────────────────


@dataclass
class PmEpic:
    """A top-level initiative, optionally linked to a Play Act."""

    id: str
    name: str
    status: str = "Backlog"
    project: str | None = None
    priority: str = "Medium"
    target_quarter: str | None = None
    owner: str | None = None
    description: str | None = None
    success_criteria: str | None = None
    notes: str | None = None
    act_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "project": self.project,
            "priority": self.priority,
            "target_quarter": self.target_quarter,
            "owner": self.owner,
            "description": self.description,
            "success_criteria": self.success_criteria,
            "notes": self.notes,
            "act_id": self.act_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row) -> PmEpic:
        return cls(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            project=row["project"],
            priority=row["priority"],
            target_quarter=row["target_quarter"],
            owner=row["owner"],
            description=row["description"],
            success_criteria=row["success_criteria"],
            notes=row["notes"],
            act_id=row["act_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class PmCycle:
    """A sprint or work session."""

    id: str
    name: str
    status: str = "Planned"
    start_date: str | None = None
    end_date: str | None = None
    goal: str | None = None
    retrospective: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "goal": self.goal,
            "retrospective": self.retrospective,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row) -> PmCycle:
        return cls(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            goal=row["goal"],
            retrospective=row["retrospective"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class PmIssue:
    """A user story, task, or bug linked to an epic."""

    id: str
    name: str
    status: str = "Backlog"
    priority: str = "Medium"
    type: str = "Feature"
    epic_id: str | None = None
    cycle_id: str | None = None
    estimate: str | None = None
    assignee: str | None = None
    forgejo_link: str | None = None
    branch: str | None = None
    acceptance_criteria: str | None = None
    notes: str | None = None
    riva_contract_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "priority": self.priority,
            "type": self.type,
            "epic_id": self.epic_id,
            "cycle_id": self.cycle_id,
            "estimate": self.estimate,
            "assignee": self.assignee,
            "forgejo_link": self.forgejo_link,
            "branch": self.branch,
            "acceptance_criteria": self.acceptance_criteria,
            "notes": self.notes,
            "riva_contract_id": self.riva_contract_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row) -> PmIssue:
        return cls(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            priority=row["priority"],
            type=row["type"],
            epic_id=row["epic_id"],
            cycle_id=row["cycle_id"],
            estimate=row["estimate"],
            assignee=row["assignee"],
            forgejo_link=row["forgejo_link"],
            branch=row["branch"],
            acceptance_criteria=row["acceptance_criteria"],
            notes=row["notes"],
            riva_contract_id=row["riva_contract_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class PmRoadmapItem:
    """A strategic planning item."""

    id: str
    name: str
    status: str = "Idea"
    quarter: str | None = None
    project: str | None = None
    description: str | None = None
    why: str | None = None
    dependencies: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "quarter": self.quarter,
            "project": self.project,
            "description": self.description,
            "why": self.why,
            "dependencies": self.dependencies,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row) -> PmRoadmapItem:
        return cls(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            quarter=row["quarter"],
            project=row["project"],
            description=row["description"],
            why=row["why"],
            dependencies=row["dependencies"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class PmResearch:
    """A decision, spike, or finding."""

    id: str
    name: str
    type: str | None = None
    status: str = "In Progress"
    project: str | None = None
    epic_id: str | None = None
    issue_id: str | None = None
    source: str | None = None
    key_finding: str | None = None
    date: str | None = None
    tags: str | None = None
    doc_path: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "project": self.project,
            "epic_id": self.epic_id,
            "issue_id": self.issue_id,
            "source": self.source,
            "key_finding": self.key_finding,
            "date": self.date,
            "tags": self.tags,
            "doc_path": self.doc_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row) -> PmResearch:
        return cls(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            status=row["status"],
            project=row["project"],
            epic_id=row["epic_id"],
            issue_id=row["issue_id"],
            source=row["source"],
            key_finding=row["key_finding"],
            date=row["date"],
            tags=row["tags"],
            doc_path=row["doc_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
