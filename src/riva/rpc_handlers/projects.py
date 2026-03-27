"""RPC handlers for RIVA project management.

Methods:
    riva/projects/create — Create a project (optional Act linkage)
    riva/projects/list — List projects
    riva/projects/get — Get project details
    riva/projects/update — Update project name/description/act link
    riva/projects/archive — Archive a project
    riva/projects/scan — Scan a directory for project subfolders
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from riva.db import get_connection, transaction
from riva.errors import RivaError

logger = logging.getLogger(__name__)


def handle_projects_create(
    *,
    name: str = "",
    description: str = "",
    act_id: str | None = None,
    **_kw,
) -> dict[str, Any]:
    """Create a new RIVA project."""
    if not name:
        raise RivaError("name is required")

    project_id = f"proj-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    with transaction() as conn:
        conn.execute(
            "INSERT INTO riva_projects "
            "(id, name, description, act_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (project_id, name, description, act_id, now, now),
        )

    logger.info("Project created: %s (%s)", name, project_id)

    return {
        "id": project_id,
        "name": name,
        "description": description,
        "act_id": act_id,
        "status": "active",
        "created_at": now,
    }


def handle_projects_list(
    *, status: str | None = None, **_kw
) -> dict[str, Any]:
    """List projects, optionally filtered by status."""
    conn = get_connection(readonly=True)
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM riva_projects WHERE status=? "
                "ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM riva_projects ORDER BY created_at DESC"
            ).fetchall()

        projects = []
        for row in rows:
            project = dict(row)
            projects.append(project)

        return {"projects": projects}
    finally:
        conn.close()


def handle_projects_get(*, project_id: str = "", **_kw) -> dict[str, Any]:
    """Get project details."""
    if not project_id:
        raise RivaError("project_id is required")

    conn = get_connection(readonly=True)
    try:
        row = conn.execute(
            "SELECT * FROM riva_projects WHERE id=?", (project_id,)
        ).fetchone()
        if row is None:
            raise RivaError(f"Project not found: {project_id}")

        project = dict(row)

        return project
    finally:
        conn.close()


def handle_projects_update(
    *,
    project_id: str = "",
    name: str | None = None,
    description: str | None = None,
    act_id: str | None = None,
    **_kw,
) -> dict[str, Any]:
    """Update project fields."""
    if not project_id:
        raise RivaError("project_id is required")

    now = datetime.now(timezone.utc).isoformat()

    updates = []
    params = []

    if name is not None:
        updates.append("name=?")
        params.append(name)
    if description is not None:
        updates.append("description=?")
        params.append(description)
    if act_id is not None:
        updates.append("act_id=?")
        params.append(act_id if act_id else None)

    if not updates:
        raise RivaError("No fields to update")

    updates.append("updated_at=?")
    params.append(now)
    params.append(project_id)

    with transaction() as conn:
        result = conn.execute(
            f"UPDATE riva_projects SET {', '.join(updates)} WHERE id=?",
            params,
        )
        if result.rowcount == 0:
            raise RivaError(f"Project not found: {project_id}")

    return handle_projects_get(project_id=project_id)


def handle_projects_archive(
    *, project_id: str = "", **_kw
) -> dict[str, Any]:
    """Archive a project."""
    if not project_id:
        raise RivaError("project_id is required")

    now = datetime.now(timezone.utc).isoformat()

    with transaction() as conn:
        result = conn.execute(
            "UPDATE riva_projects SET status='archived', updated_at=? "
            "WHERE id=? AND status='active'",
            (now, project_id),
        )
        if result.rowcount == 0:
            raise RivaError(f"Project not found or not active: {project_id}")

    logger.info("Project archived: %s", project_id)
    return {"project_id": project_id, "status": "archived"}


def handle_projects_scan(
    *, root: str = "", **_kw
) -> dict[str, Any]:
    """Scan a directory for project subfolders.

    Returns metadata about each subfolder: name, path, whether it's
    a git repo, and language hints based on marker files.
    """
    if not root:
        raise RivaError("root is required")

    root_path = Path(root)
    if not root_path.is_dir():
        raise RivaError(f"Not a directory: {root}")

    projects = []
    try:
        entries = sorted(root_path.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        raise RivaError(f"Permission denied: {root}")

    for entry in entries:
        if not entry.is_dir() or entry.name.startswith('.'):
            continue

        project: dict[str, Any] = {
            "name": entry.name,
            "path": str(entry),
            "is_git": (entry / ".git").exists(),
        }

        # Detect language/framework from marker files
        lang = _detect_language(entry)
        if lang:
            project["language"] = lang

        projects.append(project)

    return {"projects": projects, "root": root}


def _detect_language(path: Path) -> str | None:
    """Detect primary language/framework from marker files."""
    markers = {
        "pyproject.toml": "Python",
        "setup.py": "Python",
        "Cargo.toml": "Rust",
        "package.json": "Node",
        "go.mod": "Go",
        "pom.xml": "Java",
        "build.gradle": "Java",
        "Gemfile": "Ruby",
        "mix.exs": "Elixir",
        "composer.json": "PHP",
        "CMakeLists.txt": "C/C++",
        "Makefile": "Make",
    }
    for marker, lang in markers.items():
        if (path / marker).exists():
            return lang
    return None
