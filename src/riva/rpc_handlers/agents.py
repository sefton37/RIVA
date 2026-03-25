"""RPC handlers for agent management.

Methods:
    riva/agents/list — List all agents
    riva/agents/get — Get agent details
    riva/agents/create — Create a new agent with workspace
    riva/agents/delete — Delete an agent
    riva/agents/properties/get — Get agent properties
    riva/agents/properties/update — Update CLAUDE.md or permissions
    riva/agents/properties/sync — Sync properties to disk
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Module-level CCManager instance, set during service init
_manager = None


def set_manager(manager) -> None:
    """Set the module-level CCManager instance."""
    global _manager
    _manager = manager


def _get_manager():
    from riva.errors import RivaError

    if _manager is None:
        raise RivaError("CCManager not initialized")
    return _manager


def _get_username() -> str:
    return os.environ.get("USER", "unknown")


def handle_agents_list(**_kw) -> dict[str, Any]:
    """List all agents for the current user."""
    mgr = _get_manager()
    agents = mgr.list_agents(_get_username())

    # Enrich with RIVA properties status
    from riva.properties_store import get_properties

    enriched = []
    for agent in agents:
        props = get_properties(agent["id"])
        agent["has_properties"] = props is not None
        agent["synced"] = props.get("synced_at") is not None if props else False
        enriched.append(agent)

    return {"agents": enriched}


def handle_agents_get(*, agent_id: str = "", **_kw) -> dict[str, Any]:
    """Get agent details including properties."""
    if not agent_id:
        from riva.errors import RivaError

        raise RivaError("agent_id is required")

    mgr = _get_manager()
    agents = mgr.list_agents(_get_username())
    agent = next((a for a in agents if a["id"] == agent_id), None)

    if agent is None:
        from riva.errors import RivaError

        raise RivaError(f"Agent not found: {agent_id}")

    from riva.properties_store import get_properties

    props = get_properties(agent_id)
    agent["properties"] = props

    return agent


def handle_agents_create(
    *, name: str = "", purpose: str = "", **_kw
) -> dict[str, Any]:
    """Create a new agent with workspace and RIVA properties."""
    if not name:
        from riva.errors import RivaError

        raise RivaError("name is required")

    mgr = _get_manager()
    result = mgr.create_agent(_get_username(), name, purpose=purpose)

    # Create RIVA properties for this agent
    from riva.properties_store import create_properties

    create_properties(result["id"], name=name, purpose=purpose)

    return result


def handle_agents_delete(*, agent_id: str = "", **_kw) -> dict[str, Any]:
    """Delete an agent."""
    if not agent_id:
        from riva.errors import RivaError

        raise RivaError("agent_id is required")

    mgr = _get_manager()
    result = mgr.delete_agent(agent_id)

    # Properties are orphaned but not deleted (they have agent_id FK)
    # The RIVA audit trail is preserved
    return result


def handle_properties_get(*, agent_id: str = "", **_kw) -> dict[str, Any]:
    """Get agent properties (CLAUDE.md, permissions, hooks)."""
    if not agent_id:
        from riva.errors import RivaError

        raise RivaError("agent_id is required")

    from riva.properties_store import get_properties

    props = get_properties(agent_id)
    if props is None:
        from riva.errors import RivaError

        raise RivaError(f"No properties found for agent {agent_id}")

    return dict(props)


def handle_properties_update(
    *,
    agent_id: str = "",
    claude_md: str | None = None,
    permissions: dict | None = None,
    **_kw,
) -> dict[str, Any]:
    """Update agent properties. Saves to DB, sets synced_at=NULL."""
    if not agent_id:
        from riva.errors import RivaError

        raise RivaError("agent_id is required")

    from riva.properties_store import update_claude_md, update_permissions

    if claude_md is not None:
        update_claude_md(agent_id, claude_md)

    if permissions is not None:
        update_permissions(agent_id, permissions)

    from riva.properties_store import get_properties

    return get_properties(agent_id) or {}


def handle_properties_sync(*, agent_id: str = "", **_kw) -> dict[str, Any]:
    """Sync properties from DB to disk."""
    if not agent_id:
        from riva.errors import RivaError

        raise RivaError("agent_id is required")

    # Get the agent's cwd
    mgr = _get_manager()
    agents = mgr.list_agents(_get_username())
    agent = next((a for a in agents if a["id"] == agent_id), None)

    if agent is None:
        from riva.errors import RivaError

        raise RivaError(f"Agent not found: {agent_id}")

    from riva.properties_store import sync_to_disk

    return sync_to_disk(agent_id, agent["cwd"])
