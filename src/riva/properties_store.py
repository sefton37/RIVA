"""Properties Store: DB-backed source of truth for per-agent configuration.

DB is source of truth. Disk is a derived artifact.

Operations:
    get(agent_id) — read riva_agent_properties row
    create(agent_id) — create default properties for a new agent
    update_claude_md(agent_id, content) — write to DB, set synced_at=NULL
    sync_to_disk(agent_id) — write DB content to disk, detect conflicts
    get_effective_cli_args(agent_id) — convert permissions_json to CLI flags
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from riva.db import get_connection, transaction
from riva.errors import PropertiesError

logger = logging.getLogger(__name__)

_DEFAULT_CLAUDE_MD_TEMPLATE = """\
# Agent: {name}

## Purpose
{purpose}

## Conventions
- Follow existing code patterns in the workspace
- Write tests for new functionality
- Commit with descriptive messages
"""

_DEFAULT_PERMISSIONS = {
    "mode": "acceptEdits",
    "allowed_tools": [],
}


def create_properties(
    agent_id: str,
    *,
    name: str = "",
    purpose: str = "",
    claude_md: str | None = None,
) -> dict[str, Any]:
    """Create default properties for a new agent.

    Args:
        agent_id: The cc_agents.id to link to.
        name: Agent name for the CLAUDE.md template.
        purpose: Agent purpose for the CLAUDE.md template.
        claude_md: Override CLAUDE.md content (skips template).

    Returns:
        The created properties dict.
    """
    prop_id = f"prop-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    content = claude_md or _DEFAULT_CLAUDE_MD_TEMPLATE.format(
        name=name or "Unnamed", purpose=purpose or "General purpose"
    )

    with transaction() as conn:
        conn.execute(
            "INSERT INTO riva_agent_properties "
            "(id, agent_id, claude_md_content, hooks_config_json, "
            "permissions_json, env_vars_json, synced_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                prop_id,
                agent_id,
                content,
                json.dumps([]),
                json.dumps(_DEFAULT_PERMISSIONS),
                json.dumps({}),
                now,
                now,
            ),
        )

    return {
        "id": prop_id,
        "agent_id": agent_id,
        "claude_md_content": content,
        "synced_at": None,
    }


def get_properties(agent_id: str) -> dict[str, Any] | None:
    """Get properties for an agent."""
    conn = get_connection(readonly=True)
    try:
        row = conn.execute(
            "SELECT * FROM riva_agent_properties WHERE agent_id=?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def update_claude_md(agent_id: str, content: str) -> None:
    """Update CLAUDE.md content in DB. Sets synced_at=NULL."""
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        result = conn.execute(
            "UPDATE riva_agent_properties "
            "SET claude_md_content=?, synced_at=NULL, updated_at=? "
            "WHERE agent_id=?",
            (content, now, agent_id),
        )
        if result.rowcount == 0:
            raise PropertiesError(f"No properties found for agent {agent_id}")


def update_permissions(agent_id: str, permissions: dict[str, Any]) -> None:
    """Update permissions in DB."""
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        result = conn.execute(
            "UPDATE riva_agent_properties "
            "SET permissions_json=?, updated_at=? WHERE agent_id=?",
            (json.dumps(permissions), now, agent_id),
        )
        if result.rowcount == 0:
            raise PropertiesError(f"No properties found for agent {agent_id}")


def sync_to_disk(agent_id: str, agent_cwd: str) -> dict[str, Any]:
    """Write DB properties to disk in the agent's workspace.

    Detects conflicts: if the file on disk differs from the last-synced
    version, returns a conflict warning before overwriting.

    Args:
        agent_id: The agent whose properties to sync.
        agent_cwd: The agent's workspace directory path.

    Returns:
        Dict with sync status and any conflict info.
    """
    props = get_properties(agent_id)
    if props is None:
        raise PropertiesError(f"No properties found for agent {agent_id}")

    cwd = Path(agent_cwd)
    claude_md_path = cwd / "CLAUDE.md"
    db_content = props["claude_md_content"] or ""
    conflict = False

    # Check for conflict
    if claude_md_path.exists():
        disk_content = claude_md_path.read_text()
        disk_hash = hashlib.sha256(disk_content.encode()).hexdigest()

        # If synced_at is set, the last sync wrote a known version
        # If disk differs from DB content, there's a conflict
        if disk_content != db_content and props["synced_at"] is not None:
            db_hash = hashlib.sha256(db_content.encode()).hexdigest()
            if disk_hash != db_hash:
                conflict = True
                logger.warning(
                    "Properties conflict for agent %s: disk CLAUDE.md "
                    "differs from last sync",
                    agent_id,
                )

    # Write CLAUDE.md to disk
    cwd.mkdir(parents=True, exist_ok=True)
    claude_md_path.write_text(db_content)

    # Write hooks if configured
    hooks_json = props.get("hooks_config_json")
    if hooks_json:
        hooks = json.loads(hooks_json) if isinstance(hooks_json, str) else hooks_json
        if hooks:
            hooks_dir = cwd / ".claude" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            for hook in hooks:
                hook_name = hook.get("name", "hook")
                hook_content = hook.get("content", "")
                hook_path = hooks_dir / hook_name
                hook_path.write_text(hook_content)
                hook_path.chmod(0o755)

    # Git commit the sync
    try:
        subprocess.run(
            ["git", "-C", str(cwd), "add", "-A"],
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "-C", str(cwd), "commit", "-m", "chore: RIVA sync properties"],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Git is best-effort

    # Update synced_at
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        conn.execute(
            "UPDATE riva_agent_properties SET synced_at=?, updated_at=? "
            "WHERE agent_id=?",
            (now, now, agent_id),
        )

    return {
        "synced": True,
        "conflict": conflict,
        "synced_at": now,
    }


def get_effective_cli_args(agent_id: str) -> list[str]:
    """Convert permissions_json to claude CLI flags."""
    props = get_properties(agent_id)
    if props is None:
        return []

    perms_json = props.get("permissions_json")
    if not perms_json:
        return []

    perms = json.loads(perms_json) if isinstance(perms_json, str) else perms_json
    args: list[str] = []

    mode = perms.get("mode", "acceptEdits")
    if mode:
        args.extend(["--permission-mode", mode])

    for tool in perms.get("allowed_tools", []):
        args.extend(["--allowedTools", tool])

    return args
