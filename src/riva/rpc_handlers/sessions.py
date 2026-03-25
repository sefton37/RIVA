"""RPC handlers for agent session management.

Methods:
    riva/session/deploy — Sync properties then dispatch agent with contract prompt
    riva/session/poll — Poll agent events (for Tauri polling path)
    riva/session/stop — Stop a running agent
    riva/session/history — Get conversation history
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from riva.db import get_connection, transaction
from riva.errors import RivaError

logger = logging.getLogger(__name__)

_manager = None
_broker = None


def set_manager(manager) -> None:
    global _manager
    _manager = manager


def set_broker(broker) -> None:
    global _broker
    _broker = broker


def _get_manager():
    if _manager is None:
        raise RivaError("CCManager not initialized")
    return _manager


def _get_username() -> str:
    return os.environ.get("USER", "unknown")


def _build_dispatch_prompt(contract_id: str) -> str:
    """Build the prompt sent to the Claude Code agent at deploy time.

    Contains the plan title, step-by-step instructions, and acceptance
    criteria phrased as explicit goals.
    """
    from riva.contract_store import get_contract

    contract = get_contract(contract_id)
    if contract is None:
        raise RivaError(f"Contract not found: {contract_id}")

    # Get the plan for step details
    conn = get_connection(readonly=True)
    try:
        plan_row = conn.execute(
            "SELECT title, user_request FROM riva_plans WHERE id=?",
            (contract.plan_id,),
        ).fetchone()

        steps = conn.execute(
            "SELECT step_number, title, description, acceptance_criterion "
            "FROM riva_plan_steps WHERE plan_id=? ORDER BY step_number",
            (contract.plan_id,),
        ).fetchall()
    finally:
        conn.close()

    if plan_row is None:
        raise RivaError(f"Plan not found for contract: {contract_id}")

    lines = [
        f"# Task: {plan_row['title']}",
        "",
        f"Original request: {plan_row['user_request']}",
        "",
        "## Steps",
        "",
    ]

    for step in steps:
        lines.append(f"### Step {step['step_number']}: {step['title']}")
        if step["description"]:
            lines.append(step["description"])
        if step["acceptance_criterion"]:
            lines.append(f"**Goal:** {step['acceptance_criterion']}")
        lines.append("")

    lines.append("## Acceptance Criteria")
    lines.append("")
    for i, criterion in enumerate(contract.verification_criteria):
        desc = criterion.description or criterion.path or criterion.name or ""
        lines.append(f"{i + 1}. [{criterion.type}] {desc}")

    lines.append("")
    lines.append(
        "Complete all steps. Commit your work with descriptive messages."
    )

    return "\n".join(lines)


def handle_session_deploy(
    *, contract_id: str = "", agent_id: str = "", **_kw
) -> dict[str, Any]:
    """Deploy an agent with a contract prompt.

    Syncs properties to disk, then sends the contract prompt to the agent
    via CCManager.send_message().

    This handler is async-compatible: it uses run_until_complete for the
    async send_message call.
    """
    if not contract_id:
        raise RivaError("contract_id is required")
    if not agent_id:
        raise RivaError("agent_id is required")

    mgr = _get_manager()

    # Get agent cwd for property sync
    agents = mgr.list_agents(_get_username())
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if agent is None:
        raise RivaError(f"Agent not found: {agent_id}")

    # Sync properties to disk before deploying
    from riva.properties_store import get_properties, sync_to_disk

    props = get_properties(agent_id)
    if props is not None:
        sync_to_disk(agent_id, agent["cwd"])

    # Build dispatch prompt from contract
    prompt = _build_dispatch_prompt(contract_id)

    # Record session
    session_id = f"sess-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    # Get project_id from the contract's plan
    conn = get_connection(readonly=True)
    try:
        contract_row = conn.execute(
            "SELECT plan_id FROM riva_contracts WHERE id=?", (contract_id,)
        ).fetchone()
        project_id = None
        if contract_row:
            plan_row = conn.execute(
                "SELECT project_id FROM riva_plans WHERE id=?",
                (contract_row["plan_id"],),
            ).fetchone()
            project_id = plan_row["project_id"] if plan_row else None
    finally:
        conn.close()

    with transaction() as conn:
        conn.execute(
            "INSERT INTO riva_agent_sessions "
            "(id, agent_id, contract_id, project_id, started_at, "
            "status, trigger, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'running', 'riva_dispatch', ?)",
            (session_id, agent_id, contract_id, project_id, now, now),
        )

    # Send message to agent (async)
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(mgr.send_message(agent_id, prompt))
    except RuntimeError:
        # If there's already a running loop (called from async context)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(
                asyncio.run, mgr.send_message(agent_id, prompt)
            ).result(timeout=30)

    logger.info(
        "Agent %s deployed with contract %s (session %s)",
        agent_id,
        contract_id,
        session_id,
    )

    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "contract_id": contract_id,
        "status": "deployed",
    }


def handle_session_poll(
    *, agent_id: str = "", since: int = 0, **_kw
) -> dict[str, Any]:
    """Poll agent events. For the Tauri 200ms polling path."""
    if not agent_id:
        raise RivaError("agent_id is required")

    mgr = _get_manager()
    return mgr.poll_events(agent_id, since=since)


def handle_session_stop(*, agent_id: str = "", **_kw) -> dict[str, Any]:
    """Stop a running agent session."""
    if not agent_id:
        raise RivaError("agent_id is required")

    mgr = _get_manager()

    loop = asyncio.get_event_loop()
    try:
        result = loop.run_until_complete(mgr.stop_session(agent_id))
    except RuntimeError:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(
                asyncio.run, mgr.stop_session(agent_id)
            ).result(timeout=10)

    # Mark session as stopped
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        conn.execute(
            "UPDATE riva_agent_sessions SET status='stopped', "
            "completed_at=? WHERE agent_id=? AND status='running'",
            (now, agent_id),
        )

    return result


def handle_session_history(
    *, agent_id: str = "", limit: int = 100, **_kw
) -> dict[str, Any]:
    """Get conversation history for an agent."""
    if not agent_id:
        raise RivaError("agent_id is required")

    mgr = _get_manager()
    history = mgr.get_history(agent_id, limit=limit)
    return {"history": history}
