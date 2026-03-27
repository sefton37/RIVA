"""Play integration: read Act data for plan context.

Read-only in Phases 1-5. Provides:
    get_act_context(act_id) — reads Act title, description, KB page content
    get_act_list() — returns Acts for the project-linkage selector in the UI

The acts table belongs to Cairn's play_db schema and already exists in
talkingrock.db. RIVA reads it but never writes to it.
"""

from __future__ import annotations

import logging
from typing import Any

from riva.db import get_connection

logger = logging.getLogger(__name__)


def get_act_context(act_id: str) -> dict[str, Any] | None:
    """Read Act title, notes, and basic metadata for plan context.

    Args:
        act_id: The Play Act ID.

    Returns:
        Dict with act title, notes, and repo_path, or None if not found.
    """
    conn = get_connection(readonly=True)
    try:
        row = conn.execute(
            "SELECT act_id, title, notes, repo_path, artifact_type "
            "FROM acts WHERE act_id = ?",
            (act_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "act_id": row["act_id"],
            "title": row["title"],
            "notes": row["notes"] or "",
            "repo_path": row["repo_path"],
            "artifact_type": row["artifact_type"],
        }
    except Exception as exc:
        # acts table may not exist if Cairn hasn't initialized the DB
        logger.debug("Failed to read act %s: %s", act_id, exc)
        return None
    finally:
        conn.close()


def get_act_list() -> list[dict[str, Any]]:
    """Return Acts for the project-linkage selector.

    Returns:
        List of dicts with act_id and title, ordered by position.
    """
    conn = get_connection(readonly=True)
    try:
        rows = conn.execute(
            "SELECT act_id, title, active FROM acts "
            "WHERE system_role IS NULL "
            "ORDER BY position ASC"
        ).fetchall()
        return [
            {
                "act_id": row["act_id"],
                "title": row["title"],
                "active": bool(row["active"]),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.debug("Failed to list acts: %s", exc)
        return []
    finally:
        conn.close()
