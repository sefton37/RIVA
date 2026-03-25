"""RIVA schema migration.

Creates all riva_* tables in the shared talkingrock.db.
All CREATE statements use IF NOT EXISTS for idempotency.

When integrated with Cairn, this migration will be called from
play_db.py as schema v18. For standalone operation, RIVA calls
it directly at service startup.
"""

from __future__ import annotations

import logging
import sqlite3

from riva.db import get_connection

logger = logging.getLogger(__name__)

# All RIVA tables, created idempotently.
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

-- RIVA Plans: Ollama-decomposed work breakdowns
CREATE TABLE IF NOT EXISTS riva_plans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    agent_id TEXT,
    title TEXT NOT NULL,
    user_request TEXT NOT NULL,
    decomposition_json TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- RIVA Plan Steps: individual steps within a plan
CREATE TABLE IF NOT EXISTS riva_plan_steps (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    acceptance_criterion TEXT,
    estimated_minutes INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES riva_plans(id)
);

-- RIVA Contracts: approved plans become enforceable contracts
CREATE TABLE IF NOT EXISTS riva_contracts (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    verification_criteria_json TEXT,
    approved_at TEXT NOT NULL,
    approved_by TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES riva_plans(id)
);

-- RIVA Audits: post-completion verification results
CREATE TABLE IF NOT EXISTS riva_audits (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    triggered_by TEXT,
    git_diff_summary TEXT,
    files_changed_json TEXT,
    criteria_results_json TEXT,
    overall_verdict TEXT,
    verdict_explanation TEXT,
    audited_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (contract_id) REFERENCES riva_contracts(id)
);

-- RIVA Agent Properties: DB-backed source of truth for per-agent config
CREATE TABLE IF NOT EXISTS riva_agent_properties (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL UNIQUE,
    claude_md_content TEXT,
    hooks_config_json TEXT,
    permissions_json TEXT,
    env_vars_json TEXT,
    synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- RIVA Agent Sessions: links cc_history runs to contracts
CREATE TABLE IF NOT EXISTS riva_agent_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    contract_id TEXT,
    project_id TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    trigger TEXT,
    created_at TEXT NOT NULL
);
"""


# PM tables — project management, migrated from product.db.
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

-- PM Issues: user stories and tasks, optionally linked to RIVA contracts
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
    riva_contract_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (epic_id) REFERENCES pm_epics(id),
    FOREIGN KEY (cycle_id) REFERENCES pm_cycles(id),
    FOREIGN KEY (riva_contract_id) REFERENCES riva_contracts(id)
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
        logger.info("RIVA schema ensured (all riva_* and pm_* tables present)")
    finally:
        if close_after:
            conn.close()
