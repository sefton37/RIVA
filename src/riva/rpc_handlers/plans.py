"""RPC handlers for plan management.

Methods:
    riva/plan/create — Start async plan generation (guarded)
    riva/plan/status — Poll plan status
    riva/plan/get — Get full plan with steps
    riva/plan/list — List plans for a project
    riva/plan/approve — Approve plan, create contract
"""

from __future__ import annotations

import logging
from typing import Any

from riva.contract_store import create_contract
from riva.errors import PlanError

logger = logging.getLogger(__name__)

# Module-level plan engine instance, set during service init
_engine = None


def set_engine(engine) -> None:
    """Set the module-level plan engine instance."""
    global _engine
    _engine = engine


def _get_engine():
    if _engine is None:
        raise PlanError("Plan engine not initialized")
    return _engine


def handle_plan_create(
    *, project_id: str = "", user_request: str = "", **_kw
) -> dict[str, Any]:
    """Start async plan decomposition. Returns plan_id immediately."""
    if not project_id:
        raise PlanError("project_id is required")
    if not user_request:
        raise PlanError("user_request is required")

    engine = _get_engine()
    plan_id = engine.decompose(project_id, user_request)

    return {"plan_id": plan_id, "status": "draft"}


def handle_plan_status(*, plan_id: str = "", **_kw) -> dict[str, Any]:
    """Poll plan status."""
    if not plan_id:
        raise PlanError("plan_id is required")

    engine = _get_engine()
    plan = engine.get_plan(plan_id)
    if plan is None:
        raise PlanError(f"Plan not found: {plan_id}")

    return plan.to_dict()


def handle_plan_get(*, plan_id: str = "", **_kw) -> dict[str, Any]:
    """Get full plan with steps."""
    if not plan_id:
        raise PlanError("plan_id is required")

    engine = _get_engine()
    plan = engine.get_plan(plan_id)
    if plan is None:
        raise PlanError(f"Plan not found: {plan_id}")

    return plan.to_dict()


def handle_plan_list(
    *, project_id: str = "", status: str | None = None, **_kw
) -> dict[str, Any]:
    """List plans for a project."""
    if not project_id:
        raise PlanError("project_id is required")

    engine = _get_engine()
    plans = engine.list_plans(project_id, status=status)

    return {"plans": [p.to_dict() for p in plans]}


def handle_plan_approve(
    *, plan_id: str = "", agent_id: str = "", **_kw
) -> dict[str, Any]:
    """Approve plan and create a contract.

    Requires an agent_id — the agent assigned to fulfill the contract.
    """
    if not plan_id:
        raise PlanError("plan_id is required")
    if not agent_id:
        raise PlanError("agent_id is required")

    contract = create_contract(plan_id, agent_id)

    return contract.to_dict()
