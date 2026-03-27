"""Play write integration: propose Scene stage updates after passing audits.

When an audit passes, RIVA proposes marking the linked Scene as complete.
The proposal is stored in the DB and surfaced in the UI — RIVA never
writes Play state without user confirmation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from riva.db import get_connection, transaction

logger = logging.getLogger(__name__)


def propose_scene_update(
    contract_id: str,
    audit_id: str,
) -> dict[str, Any] | None:
    """Propose a Scene stage update after a passing audit.

    Checks if the contract's project is linked to a Play Act, and if
    the Act has any in_progress Scenes. If so, creates a proposal
    stored in riva_agent_sessions metadata.

    Returns:
        Proposal dict if applicable, None if no linkable Scene found.
    """
    conn = get_connection(readonly=True)
    try:
        # Get contract -> plan -> project -> act_id chain
        contract = conn.execute(
            "SELECT plan_id, agent_id FROM riva_contracts WHERE id=?",
            (contract_id,),
        ).fetchone()
        if contract is None:
            return None

        plan = conn.execute(
            "SELECT project_id FROM riva_plans WHERE id=?",
            (contract["plan_id"],),
        ).fetchone()
        if plan is None:
            return None

        project = conn.execute(
            "SELECT act_id, name FROM riva_projects WHERE id=?",
            (plan["project_id"],),
        ).fetchone()
        if project is None or not project["act_id"]:
            return None

        # Find in_progress scenes for the linked Act
        try:
            scenes = conn.execute(
                "SELECT scene_id, title FROM scenes "
                "WHERE act_id=? AND stage='in_progress' "
                "ORDER BY position ASC LIMIT 5",
                (project["act_id"],),
            ).fetchall()
        except Exception:
            # scenes table may not exist in test environments
            return None

        if not scenes:
            return None

        proposal = {
            "type": "scene_complete",
            "contract_id": contract_id,
            "audit_id": audit_id,
            "project_name": project["name"],
            "act_id": project["act_id"],
            "scenes": [
                {"scene_id": s["scene_id"], "title": s["title"]}
                for s in scenes
            ],
            "proposed_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Scene update proposed for project '%s': %d candidate scenes",
            project["name"],
            len(scenes),
        )

        return proposal

    finally:
        conn.close()


def confirm_scene_complete(scene_id: str) -> dict[str, Any]:
    """Mark a Scene as complete. Requires explicit user confirmation.

    This is the only Play write operation RIVA performs.
    """
    now = datetime.now(timezone.utc).isoformat()

    with transaction() as conn:
        result = conn.execute(
            "UPDATE scenes SET stage='complete', updated_at=? "
            "WHERE scene_id=? AND stage='in_progress'",
            (now, scene_id),
        )
        if result.rowcount == 0:
            from riva.errors import RivaError

            raise RivaError(
                f"Scene {scene_id} not found or not in_progress"
            )

    logger.info("Scene %s marked complete by RIVA", scene_id)
    return {"scene_id": scene_id, "stage": "complete"}
