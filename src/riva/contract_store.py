"""Contract Store: creates and manages enforceable contracts.

Converts an approved RivaPlan into a RivaContract. The verification
criteria are derived from plan step acceptance criteria AND translated
to NOL assembly with inline POST conditions.

The NOL layer provides:
- Content-addressable contracts (same intent = same hash)
- Structural verification via nolang assembler + verifier
- Inline POST conditions as machine-readable acceptance criteria
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from riva.db import get_connection, transaction
from riva.errors import ContractError
from riva.models import RivaContract, VerificationCriterion
from riva.nol_contract import create_nol_contract

logger = logging.getLogger(__name__)


def _parse_criterion_from_text(text: str) -> VerificationCriterion:
    """Parse a human-readable acceptance criterion into a typed criterion.

    Heuristic parsing — looks for patterns like:
    - "file_exists: src/foo.py" or "src/foo.py exists"
    - "function_defined: src/foo.py::bar" or "bar defined in src/foo.py"
    - "git_contains_change: src/" or "src/ has changes"
    - Everything else -> manual_verification
    """
    text_lower = text.lower().strip()

    # Explicit typed prefix
    if text_lower.startswith("file_exists:"):
        path = text[len("file_exists:"):].strip()
        return VerificationCriterion(type="file_exists", path=path)

    if text_lower.startswith("function_defined:"):
        spec = text[len("function_defined:"):].strip()
        if "::" in spec:
            file_part, name_part = spec.rsplit("::", 1)
            return VerificationCriterion(
                type="function_defined", file=file_part.strip(), name=name_part.strip()
            )
        return VerificationCriterion(
            type="manual_verification", description=text
        )

    if text_lower.startswith("git_contains_change:"):
        path = text[len("git_contains_change:"):].strip()
        return VerificationCriterion(type="git_contains_change", path=path)

    if text_lower.startswith("git_commit_message:"):
        keyword = text[len("git_commit_message:"):].strip()
        return VerificationCriterion(type="git_commit_message", keyword=keyword)

    # Natural language patterns
    if "exists" in text_lower and ("/" in text or "." in text):
        # Extract the path-like token
        for token in text.split():
            if "/" in token or ("." in token and not token.endswith(".")):
                return VerificationCriterion(type="file_exists", path=token.strip("\"'"))

    if "defined in" in text_lower:
        parts = text_lower.split("defined in")
        if len(parts) == 2:
            name = parts[0].strip().split()[-1] if parts[0].strip() else ""
            file_path = parts[1].strip().split()[0] if parts[1].strip() else ""
            if name and file_path:
                return VerificationCriterion(
                    type="function_defined", file=file_path, name=name
                )

    if "changes in git" in text_lower or "has changes" in text_lower:
        for token in text.split():
            if "/" in token:
                return VerificationCriterion(type="git_contains_change", path=token)

    # Default: manual verification
    return VerificationCriterion(type="manual_verification", description=text)


def create_contract(plan_id: str, agent_id: str) -> RivaContract:
    """Create a contract from an approved plan.

    Reads the plan and its steps, derives typed verification criteria
    from step acceptance criteria, translates to NOL assembly with
    inline POST conditions, and persists everything.

    Args:
        plan_id: The plan to convert.
        agent_id: The agent assigned to fulfill the contract.

    Returns:
        The created RivaContract.

    Raises:
        ContractError: If the plan doesn't exist or is not in the right state.
    """
    conn = get_connection(readonly=True)
    try:
        plan_row = conn.execute(
            "SELECT * FROM riva_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if plan_row is None:
            raise ContractError(f"Plan not found: {plan_id}")

        if plan_row["status"] not in ("pending_approval", "draft"):
            raise ContractError(
                f"Plan {plan_id} is in state '{plan_row['status']}', "
                "cannot create contract"
            )

        steps = conn.execute(
            "SELECT * FROM riva_plan_steps WHERE plan_id=? ORDER BY step_number",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()

    # Derive typed criteria from step acceptance criteria
    criteria: list[VerificationCriterion] = []
    acceptance_texts: list[str] = []

    for step in steps:
        criterion_text = step["acceptance_criterion"]
        if criterion_text:
            criteria.append(_parse_criterion_from_text(criterion_text))
            acceptance_texts.append(criterion_text)

    # NOL contract: translate criteria to assembly with POST conditions
    nol_result = create_nol_contract(
        title=plan_row["title"],
        acceptance_criteria=acceptance_texts,
        verify=True,  # Attempt verification; non-blocking if nolang unavailable
    )

    # Build contract
    contract_id = f"contract-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    # Serialize criteria + NOL data
    criteria_json = json.dumps({
        "criteria": [c.to_dict() for c in criteria],
        "nol_assembly": nol_result.assembly,
        "nol_intent_hash": nol_result.intent_hash,
        "nol_verified": nol_result.verified,
        "nol_verify_error": nol_result.verify_error,
    })

    with transaction() as conn:
        conn.execute(
            "INSERT INTO riva_contracts "
            "(id, plan_id, agent_id, verification_criteria_json, "
            "approved_at, approved_by, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'user', 'active', ?, ?)",
            (contract_id, plan_id, agent_id, criteria_json, now, now, now),
        )

        # Update plan status
        conn.execute(
            "UPDATE riva_plans SET status='approved', agent_id=?, updated_at=? "
            "WHERE id=?",
            (agent_id, now, plan_id),
        )

    contract = RivaContract(
        id=contract_id,
        plan_id=plan_id,
        agent_id=agent_id,
        verification_criteria=criteria,
        nol_assembly=nol_result.assembly,
        nol_intent_hash=nol_result.intent_hash,
        nol_verified=nol_result.verified,
        approved_at=now,
        approved_by="user",
        status="active",
        created_at=now,
        updated_at=now,
    )

    logger.info(
        "Contract %s created from plan %s (NOL hash=%s, verified=%s)",
        contract_id,
        plan_id,
        nol_result.intent_hash[:12],
        nol_result.verified,
    )

    return contract


def get_contract(contract_id: str) -> RivaContract | None:
    """Retrieve a contract from the database."""
    conn = get_connection(readonly=True)
    try:
        row = conn.execute(
            "SELECT * FROM riva_contracts WHERE id=?", (contract_id,)
        ).fetchone()
        if row is None:
            return None

        criteria_data = json.loads(row["verification_criteria_json"] or "{}")

        criteria = [
            VerificationCriterion.from_dict(c)
            for c in criteria_data.get("criteria", [])
        ]

        return RivaContract(
            id=row["id"],
            plan_id=row["plan_id"],
            agent_id=row["agent_id"],
            verification_criteria=criteria,
            nol_assembly=criteria_data.get("nol_assembly"),
            nol_intent_hash=criteria_data.get("nol_intent_hash"),
            nol_verified=criteria_data.get("nol_verified", False),
            approved_at=row["approved_at"],
            approved_by=row["approved_by"] or "user",
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    finally:
        conn.close()


def list_contracts(status: str | None = None) -> list[RivaContract]:
    """List contracts, optionally filtered by status."""
    conn = get_connection(readonly=True)
    try:
        if status:
            rows = conn.execute(
                "SELECT id FROM riva_contracts WHERE status=? "
                "ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM riva_contracts ORDER BY created_at DESC"
            ).fetchall()

        return [
            contract
            for row in rows
            if (contract := get_contract(row["id"])) is not None
        ]
    finally:
        conn.close()


def cancel_contract(contract_id: str) -> None:
    """Cancel a contract."""
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        result = conn.execute(
            "UPDATE riva_contracts SET status='cancelled', updated_at=? "
            "WHERE id=? AND status='active'",
            (now, contract_id),
        )
        if result.rowcount == 0:
            raise ContractError(
                f"Contract {contract_id} not found or not active"
            )

    logger.info("Contract %s cancelled", contract_id)
