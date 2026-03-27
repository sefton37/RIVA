"""RIVA schema migration.

Creates all riva_* and pm_* tables in the shared talkingrock.db.
All CREATE statements use IF NOT EXISTS for idempotency.
"""

from __future__ import annotations

import logging
import sqlite3

from riva.db import get_connection

logger = logging.getLogger(__name__)

# RIVA Projects table — top-level work containers.
_RIVA_TABLES_SQL = """
-- RIVA Projects: top-level work containers, optionally linked to Play Acts
CREATE TABLE IF NOT EXISTS riva_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    act_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


# PM tables — project management.
# Order matters: parent tables before child tables (FK enforcement).
_PM_TABLES_SQL = """
-- PM Epics: top-level initiatives, optionally linked to Play Acts
CREATE TABLE IF NOT EXISTS pm_epics (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Backlog',
    project TEXT,
    priority TEXT NOT NULL DEFAULT 'Medium',
    target_quarter TEXT,
    owner TEXT,
    description TEXT,
    success_criteria TEXT,
    notes TEXT,
    act_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- PM Cycles: sprints and work sessions
CREATE TABLE IF NOT EXISTS pm_cycles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Planned',
    start_date TEXT,
    end_date TEXT,
    goal TEXT,
    retrospective TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- PM Roadmap: strategic planning items
CREATE TABLE IF NOT EXISTS pm_roadmap (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Idea',
    quarter TEXT,
    project TEXT,
    description TEXT,
    why TEXT,
    dependencies TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- PM Issues: user stories and tasks
CREATE TABLE IF NOT EXISTS pm_issues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Backlog',
    priority TEXT NOT NULL DEFAULT 'Medium',
    type TEXT NOT NULL DEFAULT 'Feature',
    epic_id TEXT,
    cycle_id TEXT,
    estimate TEXT,
    assignee TEXT,
    forgejo_link TEXT,
    branch TEXT,
    acceptance_criteria TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (epic_id) REFERENCES pm_epics(id),
    FOREIGN KEY (cycle_id) REFERENCES pm_cycles(id)
);

-- PM Cycle Issues: join table for cycles and issues
CREATE TABLE IF NOT EXISTS pm_cycle_issues (
    cycle_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    PRIMARY KEY (cycle_id, issue_id),
    FOREIGN KEY (cycle_id) REFERENCES pm_cycles(id),
    FOREIGN KEY (issue_id) REFERENCES pm_issues(id)
);

-- PM Roadmap Epics: join table for roadmap items and epics
CREATE TABLE IF NOT EXISTS pm_roadmap_epics (
    roadmap_id TEXT NOT NULL,
    epic_id TEXT NOT NULL,
    PRIMARY KEY (roadmap_id, epic_id),
    FOREIGN KEY (roadmap_id) REFERENCES pm_roadmap(id),
    FOREIGN KEY (epic_id) REFERENCES pm_epics(id)
);

-- PM Research: decisions, spikes, and findings
CREATE TABLE IF NOT EXISTS pm_research (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    status TEXT NOT NULL DEFAULT 'In Progress',
    project TEXT,
    epic_id TEXT,
    issue_id TEXT,
    source TEXT,
    key_finding TEXT,
    date TEXT,
    tags TEXT,
    doc_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (epic_id) REFERENCES pm_epics(id),
    FOREIGN KEY (issue_id) REFERENCES pm_issues(id)
);
"""


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create all RIVA tables if they don't exist.

    Args:
        conn: Optional existing connection. If None, opens a new one.
    """
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True

    try:
        conn.executescript(_RIVA_TABLES_SQL)
        conn.executescript(_PM_TABLES_SQL)
        logger.info("RIVA schema ensured (riva_projects and pm_* tables present)")
    finally:
        if close_after:
            conn.close()
