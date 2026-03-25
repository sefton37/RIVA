"""RPC handlers for contract management.

Methods:
    riva/contract/get — Get a contract by ID
    riva/contract/list — List contracts (optional status filter)
    riva/contract/cancel — Cancel an active contract
"""

from __future__ import annotations

import logging
from typing import Any

from riva.contract_store import cancel_contract, get_contract, list_contracts
from riva.errors import ContractError

logger = logging.getLogger(__name__)


def handle_contract_get(*, contract_id: str = "", **_kw) -> dict[str, Any]:
    """Get a contract by ID."""
    if not contract_id:
        raise ContractError("contract_id is required")

    contract = get_contract(contract_id)
    if contract is None:
        raise ContractError(f"Contract not found: {contract_id}")

    return contract.to_dict()


def handle_contract_list(
    *, status: str | None = None, **_kw
) -> dict[str, Any]:
    """List contracts, optionally filtered by status."""
    contracts = list_contracts(status=status)
    return {"contracts": [c.to_dict() for c in contracts]}


def handle_contract_cancel(*, contract_id: str = "", **_kw) -> dict[str, Any]:
    """Cancel an active contract."""
    if not contract_id:
        raise ContractError("contract_id is required")

    cancel_contract(contract_id)
    return {"contract_id": contract_id, "status": "cancelled"}
