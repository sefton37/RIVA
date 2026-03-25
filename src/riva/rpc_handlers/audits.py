"""RPC handlers for audit management.

Methods:
    riva/audit/trigger — Manually trigger an audit for a contract
    riva/audit/get — Get an audit result
    riva/audit/list — List audits (optional contract filter)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from riva.audit_engine import get_audit, list_audits, run_audit
from riva.errors import AuditError

logger = logging.getLogger(__name__)

_manager = None


def set_manager(manager) -> None:
    global _manager
    _manager = manager


def _get_username() -> str:
    return os.environ.get("USER", "unknown")


def handle_audit_trigger(
    *, contract_id: str = "", **_kw
) -> dict[str, Any]:
    """Manually trigger an audit for a contract."""
    if not contract_id:
        raise AuditError("contract_id is required")

    # Get the agent's cwd
    from riva.contract_store import get_contract

    contract = get_contract(contract_id)
    if contract is None:
        raise AuditError(f"Contract not found: {contract_id}")

    if _manager is None:
        raise AuditError("CCManager not initialized")

    agents = _manager.list_agents(_get_username())
    agent = next((a for a in agents if a["id"] == contract.agent_id), None)
    if agent is None:
        raise AuditError(f"Agent not found: {contract.agent_id}")

    return run_audit(contract_id, agent["cwd"], triggered_by="user")


def handle_audit_get(*, audit_id: str = "", **_kw) -> dict[str, Any]:
    """Get an audit result."""
    if not audit_id:
        raise AuditError("audit_id is required")

    audit = get_audit(audit_id)
    if audit is None:
        raise AuditError(f"Audit not found: {audit_id}")

    return audit


def handle_audit_list(
    *, contract_id: str | None = None, **_kw
) -> dict[str, Any]:
    """List audits, optionally filtered by contract."""
    audits = list_audits(contract_id=contract_id)
    return {"audits": audits}
