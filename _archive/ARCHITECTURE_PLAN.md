# Plan: RIVA — Recursive Intent Verification Architecture
## Clean-Slate Implementation

**Date:** 2026-03-13
**Status:** Proposed — awaiting approval before any implementation begins
**Replaces:** Archived code-mode-centric implementation in `RIVA/archive/`

All paths in this document are relative to their respective repo roots unless
stated otherwise. Cairn repo = `/home/kellogg/dev/Cairn/`. RIVA repo =
`/home/kellogg/dev/RIVA/`.

---

## Table of Contents

1. [Context](#context)
2. [Critical Pre-Discovery: CCManager](#critical-pre-discovery-ccmanager)
3. [Architecture Overview](#architecture-overview)
4. [Data Model](#data-model)
5. [Core Subsystems](#core-subsystems)
6. [Integration Points](#integration-points)
7. [Alternative Approaches](#alternative-approaches)
8. [Phased Implementation Plan](#phased-implementation-plan)
9. [Files Affected — Complete List](#files-affected--complete-list)
10. [Risks and Mitigations](#risks-and-mitigations)
11. [Testing Strategy](#testing-strategy)
12. [Definition of Done](#definition-of-done)
13. [Confidence Assessment](#confidence-assessment)
14. [Unknowns Requiring Validation Before Phase 1](#unknowns-requiring-validation-before-phase-1)

---

## Context

### What Exists Today

**RIVA repo** is essentially empty: a `pyproject.toml` with no dependencies, a
one-line `__init__.py`, an ADR about verification backport, and an archived
code-mode implementation that is 100% stale. The slate is clean.

**Cairn** already contains significant infrastructure that RIVA must reuse rather
than reinvent:

- `src/cairn/services/cc_manager.py` — `CCManager`: full Claude Code lifecycle
  (spawn, stdout parse, event buffer, session resume via `--resume`, workspace
  creation with CLAUDE.md + git init). Already battle-tested in Helm and the
  Cairn Tauri cc view.
- `src/cairn/services/cc_session_observer.py` — Post-session analysis: memory
  extraction, PM insight extraction, scene note updates. Fire-and-forget
  background queue.
- `src/cairn/rpc_handlers/cc.py` — Seven RPC endpoints: `cc/agents/list`,
  `cc/agents/create`, `cc/agents/delete`, `cc/session/send`, `cc/session/poll`,
  `cc/session/stop`, `cc/session/history`.
- `src/cairn/play_db.py` — Schema v17, WAL-mode SQLite. Tables `cc_agents`
  (id, name, slug, purpose, cwd, session_id, linked_scene_id, username) and
  `cc_history` (agent_id, role, content) already exist.
- `apps/cairn-tauri/src/rivaView.ts` — 62-line stub returning "Coming soon — Phase 1".
- `apps/cairn-tauri/src/agentBar.ts` — `AgentId` type already includes `'riva'`;
  `CORE_AGENTS` array already lists RIVA with icon and description.

**trcore** (`/home/kellogg/dev/talkingrock-core/`) provides: `providers/`
(Ollama, base, factory, `quick_judge`), `atomic_ops/` (classifier, decomposer,
executor, verifiers with directives), `db.py`, `config.py`, `errors.py`,
`security.py`, `certainty.py`.

**ADR-VERIFICATION-BACKPORT.md** (dated 2026-03-04) specifies that the
entry-point safety judge + intent clarity check via `trcore.providers.quick_judge`
must be built into Phase 1 — not retrofitted.

### Why This Build Is Needed

The archived RIVA was a code verification system (code-mode). The pivoted RIVA
(2026-03-08) is a PM-and-agent-orchestrator. It needs to be built from scratch
with the correct mission: decompose intent into contracts, dispatch Claude Code
agents, supervise them live, audit their output, and manage their properties
(CLAUDE.md, hooks, permissions).

---

## Critical Pre-Discovery: CCManager

**This is the most important finding from reconnaissance.**

`CCManager` in `cairn.services.cc_manager` already handles most of what RIVA
needs for agent process management:

| Capability | CCManager Status |
|---|---|
| Spawn `claude --print --output-format stream-json` | Done |
| Parse stream-json (assistant_delta, tool_use, tool_result, result) | Done |
| Buffer events for frontend polling | Done |
| Session resume via `--resume session_id` | Done |
| Workspace creation (dir, CLAUDE.md, README, .gitignore, git init) | Done |
| Persist agent config to `cc_agents` in `talkingrock.db` | Done |
| Conversation history in `cc_history` | Done |
| Post-session observer (insight extraction, memory, scene notes) | Done |

**What CCManager does NOT do (RIVA's value add):**

| Capability | Status |
|---|---|
| Contracts (acceptance criteria per task) | Not built |
| Audit (git diff against contract after completion) | Not built |
| Plan Engine (Ollama-powered intent decomposition) | Not built |
| Properties Store (CLAUDE.md/hooks/permissions as DB source of truth) | Partial — CCManager writes basic CLAUDE.md at creation only |
| Entry guard (safety + intent judges before plan generation) | Not built |
| Project/Act linkage (RIVA projects mapped to Play Acts) | Not built |
| Streaming to Textual TUI | Not built |
| RIVA-specific RPC handlers | Not built |
| RIVA backend service process | Not built |

**Consequence:** RIVA does NOT reimplement process management. It wraps,
extends, and orchestrates what CCManager already provides. RIVA's Python backend
imports `CCManager` directly. No duplication of subprocess management,
stream-json parsing, or event buffering.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      USER INTERFACES                         │
│                                                              │
│  ┌────────────────────────┐   ┌───────────────────────────┐  │
│  │  Cairn Tauri App        │   │  Standalone Textual TUI   │  │
│  │  rivaView.ts            │   │  src/riva/tui/app.py      │  │
│  │                         │   │                           │  │
│  │  LEFT: Observatory      │   │  LEFT: Observatory        │  │
│  │  - Agent list           │   │  - Agent list             │  │
│  │  - Live stream view     │   │  - Live stream view       │  │
│  │  - Properties panel     │   │  - Properties panel       │  │
│  │                         │   │                           │  │
│  │  RIGHT: Chat with RIVA  │   │  RIGHT: Chat with RIVA    │  │
│  │  - Conversation         │   │  - Conversation           │  │
│  │  - Plan display         │   │  - Plan display           │  │
│  │  - Contract review      │   │  - Contract review        │  │
│  │  - Audit results        │   │  - Audit results          │  │
│  └───────────┬────────────┘   └──────────────┬────────────┘  │
│              │ Tauri RPC                      │ Unix Socket   │
│              │ kernel_request / events        │ JSON-RPC 2.0  │
└──────────────┼────────────────────────────────┼───────────────┘
               │                                │
               └───────────────┬────────────────┘
                               │
               ┌───────────────▼──────────────────────┐
               │         RIVA BACKEND SERVICE          │
               │                                       │
               │  src/riva/service.py                  │
               │  (asyncio + Unix domain socket)       │
               │                                       │
               │  ┌────────────────────────────────┐  │
               │  │ RPC Layer                       │  │
               │  │ src/riva/rpc_dispatcher.py      │  │
               │  │ src/riva/rpc_handlers/          │  │
               │  └─────────────┬──────────────────┘  │
               │                │                      │
               │  ┌─────────────▼──────────────────┐  │
               │  │ Core Subsystems                 │  │
               │  │                                 │  │
               │  │ EntryGuard       PlanEngine     │  │
               │  │ ContractStore    AuditEngine    │  │
               │  │ PropertiesStore  StreamBroker   │  │
               │  │ PlayIntegration                 │  │
               │  └─────────────┬──────────────────┘  │
               │                │                      │
               │  ┌─────────────▼──────────────────┐  │
               │  │ CCManager (imported from Cairn) │  │
               │  │  Agent spawn, poll, stop        │  │
               │  │  Event buffering, session resume│  │
               │  └─────────────┬──────────────────┘  │
               └────────────────┼──────────────────────┘
                                │
               ┌────────────────▼──────────────────────┐
               │          AGENT PROCESSES               │
               │                                        │
               │  claude --print --output-format        │
               │         stream-json --resume SESSION   │
               │                                        │
               │  Agent 1    Agent 2    Agent N         │
               │  cwd/       cwd/       cwd/            │
               │  CLAUDE.md  CLAUDE.md  CLAUDE.md       │
               │  hooks/     hooks/     hooks/          │
               └────────────────┬──────────────────────┘
                                │
               ┌────────────────▼──────────────────────┐
               │          EXTERNAL SYSTEMS              │
               │                                        │
               │  Git (audit: diff, log, status)        │
               │  Ollama (plan engine, quick_judge)     │
               │  Play/Acts (project context linkage)   │
               │  talkingrock.db (SQLite WAL)           │
               └────────────────────────────────────────┘
```

### Key Architectural Decisions

**RIVA backend runs as a separate Python process**, not embedded inside
Cairn's `ui_rpc_server.py`. Rationale: RIVA needs background threads
(stream broker, audit queue, observer). Cairn's RPC server is a synchronous
stdio loop — embedding RIVA there would block Cairn's response path.
The Tauri frontend routes `riva/*` calls through a thin proxy in Cairn's
dispatcher that forwards to RIVA's Unix socket.

**CCManager is reused from Cairn, not duplicated.** RIVA imports it.
Since RIVA runs as a separate process, it has its own `CCManager` instance
and its own `_procs` dict (in-memory). `talkingrock.db` is the shared durable
state; WAL mode handles concurrent access safely.

**Properties (CLAUDE.md, hooks, permissions) are DB-backed.** At deploy time,
RIVA writes them to disk in the agent's workspace. The UI reads from DB, not
from disk. RIVA never reads CLAUDE.md from disk to populate its interface.

**Streaming to Textual TUI uses async queues (no polling).** Since the TUI
and RIVA service share an asyncio event loop, the StreamBroker can push
events directly into `asyncio.Queue` objects. Tauri uses polling
(`riva/session/poll`) at 200ms, consistent with the existing `cc/session/poll`
pattern.

---

## Data Model

All tables live in `~/.talkingrock/talkingrock.db` (WAL mode), the shared
database used by Cairn, ReOS, and RIVA. RIVA additions increment the schema
version from the current v17 to v18.

### Existing tables (Cairn, schema v16+, already in place)

```sql
cc_agents (
    id TEXT PRIMARY KEY,
    username TEXT,
    name TEXT,
    slug TEXT UNIQUE,
    purpose TEXT,
    cwd TEXT,
    session_id TEXT,
    linked_scene_id TEXT,
    created_at TEXT,
    updated_at TEXT
)

cc_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT,
    role TEXT,        -- 'user' | 'assistant' | 'error'
    content TEXT,
    created_at TEXT
)

cc_insights (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    type TEXT,        -- 'tracking' | 'lesson' | 'pattern' | 'decision'
    text TEXT,
    session_date TEXT,
    created_at TEXT
)
```

### New Tables (schema v18, added by RIVA migration)

```sql
-- RIVA Projects: top-level work containers, optionally linked to Play Acts
riva_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    act_id TEXT,                    -- nullable FK to acts.act_id
    status TEXT DEFAULT 'active',   -- 'active' | 'archived' | 'complete'
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)

-- RIVA Plans: Ollama-decomposed work breakdowns
riva_plans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,       -- FK riva_projects.id
    agent_id TEXT,                  -- nullable FK cc_agents.id (null until assigned)
    title TEXT NOT NULL,
    user_request TEXT NOT NULL,     -- original natural language
    decomposition_json TEXT,        -- full Ollama output: steps, estimates, risks
    status TEXT DEFAULT 'draft',
    -- 'draft' | 'pending_approval' | 'approved' | 'executing'
    -- | 'complete' | 'failed' | 'cancelled'
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)

-- RIVA Plan Steps: individual steps within a plan
riva_plan_steps (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,          -- FK riva_plans.id
    step_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    acceptance_criterion TEXT,      -- verifiable condition for this step
    estimated_minutes INTEGER,
    status TEXT DEFAULT 'pending',
    -- 'pending' | 'in_progress' | 'complete' | 'failed' | 'skipped'
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)

-- RIVA Contracts: approved plans become enforceable contracts
riva_contracts (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,   -- FK riva_plans.id
    agent_id TEXT NOT NULL,         -- FK cc_agents.id (assigned at approval)
    verification_criteria_json TEXT,-- structured criteria derived from plan steps
    approved_at TEXT NOT NULL,
    approved_by TEXT,               -- 'user' in all current flows
    status TEXT DEFAULT 'active',
    -- 'active' | 'fulfilled' | 'violated' | 'cancelled'
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)

-- RIVA Audits: post-completion verification results
riva_audits (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,      -- FK riva_contracts.id
    agent_id TEXT NOT NULL,         -- FK cc_agents.id
    triggered_by TEXT,              -- 'user' | 'auto'
    git_diff_summary TEXT,
    files_changed_json TEXT,
    criteria_results_json TEXT,     -- per-criterion pass/fail with evidence
    overall_verdict TEXT,
    -- 'passed' | 'failed' | 'partial' | 'inconclusive'
    verdict_explanation TEXT,
    audited_at TEXT NOT NULL,
    created_at TEXT NOT NULL
)

-- RIVA Agent Properties: DB-backed source of truth for per-agent config
riva_agent_properties (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL UNIQUE,  -- FK cc_agents.id
    claude_md_content TEXT,         -- full CLAUDE.md text (synced to disk at deploy)
    hooks_config_json TEXT,         -- hook definitions
    permissions_json TEXT,          -- --permission-mode and allowed-tools
    env_vars_json TEXT,             -- non-secret env overrides
    synced_at TEXT,                 -- NULL when DB is ahead of disk
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)

-- RIVA Agent Sessions: links cc_history runs to contracts
riva_agent_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,         -- FK cc_agents.id
    contract_id TEXT,               -- nullable FK riva_contracts.id
    project_id TEXT,                -- FK riva_projects.id
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT DEFAULT 'running',  -- 'running' | 'complete' | 'stopped' | 'error'
    trigger TEXT,                   -- 'user_manual' | 'riva_dispatch'
    created_at TEXT NOT NULL
)
```

### Schema Migration Strategy

RIVA adds to `play_db.py`'s migration block as schema version 18. Each new
table is created with `IF NOT EXISTS`. The migration guard in `play_db.py`
already uses `PRAGMA user_version` to track the current version. RIVA's
service startup calls the same migration entry point that Cairn calls — the
migration is idempotent so double-run is harmless. RIVA must NOT define a
separate migration runner that races with Cairn's.

---

## Core Subsystems

### 1. Entry Guard

**Location:** `src/riva/entry_guard.py`

Required from Phase 1 per ADR-VERIFICATION-BACKPORT.md. Gates all user messages
before they reach the plan engine.

**Flow:**

```
User message
  -> safety_judge (quick_judge with SAFETY_JUDGE_SYSTEM)
       returns False -> boundary response, do not proceed
       returns True  -> intent_judge (quick_judge with INTENT_JUDGE_SYSTEM)
                          returns False -> clarification request
                          returns True  -> proceed to PlanEngine
```

Uses `trcore.providers.quick_judge.quick_judge`. Proven at ~200-500ms per call
on llama3.1:8b. Both judges are fail-open (Ollama failure means proceed). The
judges run sequentially — safety must clear before intent is checked.

### 2. Plan Engine

**Location:** `src/riva/plan_engine.py`

Takes a user request and project context, calls Ollama, returns a structured
`RivaPlan` with numbered steps each carrying a verifiable `acceptance_criterion`.

**Key design constraint on acceptance criteria:** Each criterion must describe
something the Audit Engine can check without running code: a file that should
exist, a function that should be defined, a git commit that should mention a
specific change, or a test file that should have been created. Criteria like
"the feature works correctly" are rejected during plan review — the plan engine
prompt explicitly prohibits them.

**Async dispatch pattern:** `riva/plan/create` returns a `plan_id` immediately
and runs Ollama in a background asyncio task. The client polls
`riva/plan/status` until status transitions from `draft` to
`pending_approval`. This prevents the RPC thread from blocking for 10-60
seconds on LLM inference.

**Revision loop:** If acceptance criteria are ambiguous (a third quick_judge
call with a custom criterion-clarity prompt), RIVA asks the user to clarify
before saving. Maximum two revision cycles before saving as-is with a
`needs_review` flag.

### 3. Contract Store

**Location:** `src/riva/contract_store.py`

Converts an approved `RivaPlan` into an enforceable `RivaContract`. The
`verification_criteria_json` field is derived from the plan's step acceptance
criteria, structured as a list of typed criterion objects:

```json
[
  {"type": "file_exists", "path": "src/service.py"},
  {"type": "function_defined", "file": "src/service.py", "name": "handle_foo"},
  {"type": "git_contains_change", "path": "src/service.py"},
  {"type": "manual_verification", "description": "all tests pass"}
]
```

Contract lifecycle: `active` -> `fulfilled` (audit passed) or `violated`
(audit failed) or `cancelled` (user cancelled).

### 4. Audit Engine

**Location:** `src/riva/audit_engine.py`

Verifies contract criteria after agent completion. Uses `subprocess` to run
git commands in the agent workspace. No git library dependency.

**Criterion evaluators:**

| Type | Mechanism |
|---|---|
| `file_exists` | `Path(agent_cwd / path).exists()` |
| `function_defined` | `grep -n "def name"` in file |
| `git_contains_change` | `git -C cwd diff --name-only HEAD~N` includes path |
| `git_commit_message` | `git -C cwd log --oneline -N` contains keyword |
| `manual_verification` | Always `inconclusive` — flagged for user |

The value `N` (how many commits back) is derived from commits since the
`riva_agent_sessions.started_at` timestamp via
`git -C cwd log --since=TIMESTAMP --oneline`.

If the workspace has no git history, git-based criteria return
`inconclusive` (not `failed`). File-system criteria still run.

Auto-trigger: when the `StreamBroker` delivers a `done` event for an agent
that has an active contract, the audit runs automatically in a background task.

### 5. Stream Broker

**Location:** `src/riva/stream_broker.py`

Pub/sub layer over `CCManager._procs[agent_id].events`. Solves the problem
that CCManager buffers events in a list while multiple consumers need them.

**Design:**
- Each subscriber calls `StreamBroker.subscribe(agent_id)` and gets back an
  `asyncio.Queue`.
- A per-agent background task (`_watch_agent`) polls
  `CCManager._procs[agent_id].events[last_index:]` at 50ms intervals and
  pushes new events to all subscriber queues.
- The Textual TUI consumes queues directly (no polling overhead).
- The Tauri path uses `riva/session/poll` (wraps `CCManager.poll_events`),
  which doesn't go through the broker — same polling model as existing
  `cc/session/poll`.
- When a `done` event is pushed to a queue, the broker notifies the audit
  engine (if a contract is active) and cleans up the watcher task.

### 6. Properties Store

**Location:** `src/riva/properties_store.py`

DB is source of truth. Disk is a derived artifact.

**Operations:**
- `get(agent_id)` — reads `riva_agent_properties` row
- `update_claude_md(agent_id, content)` — writes to DB, sets `synced_at = NULL`
- `sync_to_disk(agent_id)` — writes CLAUDE.md to `{agent.cwd}/CLAUDE.md`;
  if the file's current hash differs from last-synced hash, warns of conflict
  before overwriting; updates `synced_at`; commits via git
- `deploy(agent_id, contract_id)` — calls `sync_to_disk`, then calls
  `CCManager.send_message` with the contract's dispatch prompt
- `get_effective_cli_args(agent_id)` — converts `permissions_json` to a list
  of `claude` CLI flags

**Default CLAUDE.md template** (richer than CCManager's basic version):
includes the agent's purpose, the contract summary (inserted at deploy time),
project context, and a link to the global CLAUDE.md conventions.

**Hook files:** Stored as JSON in `hooks_config_json`. On `sync_to_disk`,
each hook is written as a script to the agent workspace's local hook path
(project-scoped hooks, not user-global). Writing to `~/.claude/hooks/` is
explicitly out of scope for RIVA — that is the user's global domain.

### 7. Play Integration

**Location:** `src/riva/play_integration.py`

Read-only in Phases 1-5. Provides:
- `get_act_context(act_id)` — reads Act title, description, and KB page
  content for use as plan generation context
- `get_act_list()` — returns Acts for the project-linkage selector in the UI

Write integration (proposing Scene stage updates after a passing audit) is
Phase 6 and requires user confirmation before any `play/scenes/update` call.

---

## Integration Points

### A. RIVA Backend to Cairn Backend (RPC Forwarding)

**Problem:** The Tauri frontend talks to Cairn via JSON-RPC over stdio. RIVA
is a separate process. The frontend must be able to reach RIVA.

**Solution:** Add a `riva/*` namespace to Cairn's `ui_rpc_server.py` that
proxies to RIVA's Unix socket. A single handler in Cairn:
`handle_riva_proxy(db, *, method, params)` opens a connection to
`~/.talkingrock/riva.sock`, sends the full JSON-RPC request, waits for the
response, and returns it. All `riva/*` methods in the dispatch table route
to this proxy.

If RIVA's socket is not present or connection is refused, the proxy handler
returns a structured error `{"code": -32099, "message": "RIVA service not running"}`
rather than crashing Cairn.

**Alternative rejected:** Embedding RIVA as a Python module inside Cairn's
process. Rejected because RIVA has background threads that would block Cairn's
synchronous stdio loop, and because RIVA must be independently launchable
from the Textual TUI without Cairn.

### B. RIVA Backend to Textual TUI

**Transport:** Unix domain socket at `~/.talkingrock/riva.sock`.

**Protocol:** JSON-RPC 2.0, same as Cairn's stdio protocol but over socket.

**Real-time events:** The Textual TUI runs within the same asyncio event loop
as the RIVA service (TUI starts the service inline if the socket is not
already responding). The TUI's stream pane widgets read from `asyncio.Queue`
objects fed by the StreamBroker. No polling needed — true push.

**TUI startup logic:**
1. Try to connect to `~/.talkingrock/riva.sock`
2. If connection refused: start `riva.service` as a background asyncio task
3. If connected: attach to running instance
4. Show "Connected" / "Connecting..." / "Offline" status badge

### C. RIVA Backend to Tauri Frontend (through Cairn bridge)

Cairn forwards `riva/*` RPC calls as described in A. For streaming, the Tauri
frontend polls `riva/session/poll` at 200ms intervals — the same proven
pattern as `cc/session/poll`. This is adequate for project management (not
interactive terminal). True push via Tauri events is a Phase 6 option once
the ReOS PTY plan has proven that pattern in production.

### D. RIVA to Claude Code CLI

**Uses CCManager directly.** No new subprocess management. RIVA calls:
- `CCManager.send_message(agent_id, prompt)` at deploy time
- `CCManager.poll_events(agent_id, since)` via the `riva/session/poll` handler
- `CCManager.stop_session(agent_id)` via `riva/session/stop`

The `prompt` sent at deploy time is the contract dispatch prompt: plan title,
step-by-step instructions, and acceptance criteria phrased as explicit goals
for the agent.

**Permission mode:** Derived from `riva_agent_properties.permissions_json`.
Default is `acceptEdits`. The `bypass` mode requires explicit per-agent
opt-in set by the user in the Properties panel.

### E. RIVA to Git (Audit Verification)

Subprocess calls only. No git library. Commands run with `git -C {agent_cwd}`:

```
git log --oneline -20
git log --since=TIMESTAMP --oneline
git diff --stat HEAD~N
git diff --name-only HEAD~N
git show HEAD:{relative_path}
```

All subprocess calls set `timeout=10`, `capture_output=True`. Non-zero return
codes are treated as `inconclusive` for that criterion, never as a hard error
that aborts the audit.

### F. RIVA to Project Filesystems (Properties Sync)

`PropertiesStore.sync_to_disk` writes only to the agent's workspace directory
(`cc_agents.cwd`):
- `{cwd}/CLAUDE.md` — from `riva_agent_properties.claude_md_content`
- `{cwd}/.claude/hooks/` — one file per hook in `hooks_config_json`

After writing, runs `git -C {cwd} add -A && git commit -m "chore: RIVA sync properties"`.
This ensures the audit's git diff reflects only agent work, not property syncs
(the sync commit is excluded from the diff window by timestamp).

RIVA never writes to `~/.claude/CLAUDE.md` (the user's global file).

---

## Alternative Approaches

### A1. How RIVA Launches and Communicates With Claude Code Agents

**Option 1 (Recommended): Use existing CCManager**

Import `cairn.services.cc_manager.CCManager` directly. Call `send_message`,
`poll_events`, `stop_session`. No new subprocess code.

- Complexity: Low — CCManager is proven, no new parsing code needed
- Risk: Low — already in production for Helm and cc views
- Alignment: High — follows existing patterns; any CCManager improvements
  benefit RIVA automatically
- Coupling: RIVA depends on a Cairn internal. Acceptable because both are in
  the same monorepo under the same developer's control. If CCManager moves to
  `trcore`, RIVA updates its import path — a one-line change.

**Option 2: RIVA has its own subprocess manager**

Reimplement `claude` process spawning and stream-json parsing in
`src/riva/agent_runner.py`.

- Complexity: Medium — stream-json parsing is subtle (partial messages,
  tool_use blocks, session_id extraction)
- Risk: Medium — duplicating working logic introduces new bugs
- Alignment: Low — violates DRY across the ecosystem
- Upside: zero coupling to Cairn internals
- **Rejected.** The coupling cost is lower than the duplication risk.

---

### A2. How Real-Time Streaming Works

**Option 1 (Recommended): CCManager polling for Tauri; asyncio queue for TUI**

Tauri polls `riva/session/poll` every 200ms (same as existing `cc/session/poll`
pattern). TUI subscribes to `asyncio.Queue` via StreamBroker (zero latency).

- Complexity: Low — polling is proven; queue consumption is idiomatic asyncio
- Alignment: High — same pattern as Helm and cc views
- Downside: 200ms max latency in Tauri. For project management, not interactive
  terminal, this is imperceptible.

**Option 2: Tauri events for push streaming**

When Tauri subscribes, RIVA's Cairn proxy emits `riva://agent-event` as Tauri
events using `AppHandle.emit()` from a background thread (same architecture
as the ReOS PTY plan's `reos://pty-output`).

- Complexity: Medium — Tauri event capability declarations, AppHandle threading
- Alignment: Medium — follows ReOS architecture, but that architecture has not
  yet been built and validated
- Upside: true push, no polling latency
- **Deferred to Phase 6.** Once the ReOS PTY path proves this pattern in
  production, RIVA can adopt it. The polling path is safe and correct for now.

---

### A3. How the Textual TUI and Tauri View Share UI Logic

**Option 1 (Recommended): Separate implementations, shared backend protocol**

`rivaView.ts` (TypeScript/DOM) and `src/riva/tui/` (Python/Textual) are
entirely separate implementations. They share nothing in the UI layer. They
share everything in the backend: same Unix socket, same JSON-RPC methods,
same data structures.

- Complexity: Each UI is written for its native paradigm — no impedance mismatch
- Alignment: High — this is the Cairn philosophy (Tauri app and Textual TUI
  are always written separately)
- Downside: UI features must be implemented twice
- **Chosen.** The protocol is the contract; the UI code is not.

**Option 2: Textual rendered in a browser embedded in Tauri**

Use Textual's experimental web rendering mode inside a Tauri webview.

- Complexity: Very high — Textual's `serve` mode is experimental and not
  intended for production embedding
- Risk: High — the paradigm mismatch would create fragile, hard-to-maintain code
- **Rejected immediately.**

**Option 3: RIVA exposes HTTP, Tauri talks HTTP directly**

RIVA uses FastAPI with an HTTP transport. Tauri frontend calls HTTP directly,
bypassing the Cairn stdio bridge.

- Complexity: Medium
- Alignment: Low — Cairn's `ui_rpc_server.py` explicitly states "no network
  listener" as a design goal. Adding a network port for RIVA contradicts this.
- **Rejected.** The Unix socket + proxy approach is correct.

---

### A4. How Play Integration Works

**Option 1 (Recommended): Loose coupling via optional `act_id` FK**

`riva_projects.act_id` is nullable. RIVA reads Act data for plan context.
RIVA proposes Scene updates after audit passes, but the user must confirm
before any Play write happens. The Play schema is not modified.

- Complexity: Low in Phases 1-5 (read-only)
- Risk: Low — Play data is protected from unilateral mutation
- Alignment: High — consistent with Cairn's "AI proposes, human approves"
  philosophy

**Option 2: RIVA auto-creates Play Acts for new projects**

When a RIVA project is created, automatically create a Play Act.

- Risk: Medium — pollutes the user's Play with RIVA-generated Acts. Play Acts
  are personal life narratives; RIVA projects are technical work. These overlap
  but are not the same concept.
- **Deferred to Phase 6** as a user-configurable opt-in setting, not default.

**Option 3: RIVA projects ARE Play Acts (full unification)**

No `riva_projects` table. RIVA projects are stored as Acts with RIVA-specific
columns added to the `acts` table.

- Risk: High — tightly couples RIVA's technical project model to Play's
  personal life organization model. Forces Play to understand RIVA semantics.
- **Rejected.** Separation of concerns.

---

## Phased Implementation Plan

---

### Phase 1: Foundation — Backend Service + Entry Guard

**What it delivers:** A running RIVA service at `~/.talkingrock/riva.sock`
that can receive RPC calls, apply the entry guard (safety + intent judges),
and return system status. The Tauri `rivaView.ts` shows a minimal split-screen
shell with empty panels. The Textual TUI can connect, show connection status,
and exchange a ping. Schema v18 tables exist in the DB.

This phase is infrastructure only. No plan generation, no agents, no contracts.

**Architecture decisions this phase:**
- `src/riva/service.py`: asyncio server on `~/.talkingrock/riva.sock`
- `src/riva/rpc_dispatcher.py`: JSON-RPC 2.0 dispatch, entry guard runs before
  every plan-related method (enforced at dispatcher level, not per-handler)
- `src/riva/entry_guard.py`: safety judge then intent judge via `quick_judge`
- `src/riva/schema.py`: idempotent v18 migration, called at service start
- Cairn `src/cairn/rpc_handlers/riva.py`: thin Unix socket proxy handler
- Cairn `ui_rpc_server.py`: register `riva/*` namespace to proxy

**Files to create (RIVA repo):**

| File | Purpose |
|---|---|
| `src/riva/service.py` | asyncio Unix socket server |
| `src/riva/rpc_dispatcher.py` | JSON-RPC 2.0 dispatch table |
| `src/riva/entry_guard.py` | Safety + intent judges |
| `src/riva/db.py` | DB access via trcore.db |
| `src/riva/schema.py` | v18 migration: all riva_* tables |
| `src/riva/errors.py` | RIVA-specific error types |
| `src/riva/rpc_handlers/__init__.py` | Handler package |
| `src/riva/rpc_handlers/system.py` | `riva/ping`, `riva/status` |
| `src/riva/tui/__init__.py` | TUI package |
| `src/riva/tui/app.py` | Minimal Textual app, connection logic |
| `src/riva/tui/panes/__init__.py` | Panes package |
| `src/riva/tui/panes/left.py` | Observatory pane (stub) |
| `src/riva/tui/panes/right.py` | Chat pane (stub) |
| `tests/__init__.py` | Test package |
| `tests/test_entry_guard.py` | Entry guard unit tests |
| `tests/test_rpc_dispatcher.py` | Dispatcher unit tests |

**Files to modify (RIVA repo):**

| File | Change |
|---|---|
| `pyproject.toml` | Add deps: textual, trcore (editable); add `riva` entry point CLI |

**Files to create/modify (Cairn repo):**

| File | Change |
|---|---|
| `src/cairn/rpc_handlers/riva.py` | Create: proxy handler to RIVA socket |
| `src/cairn/ui_rpc_server.py` | Register `riva/*` namespace to proxy |
| `src/cairn/play_db.py` | Add v18 migration block for riva_* tables |
| `apps/cairn-tauri/src/rivaView.ts` | Replace stub with split-screen shell |

**Dependencies:** None. This is the foundation.

**Testing strategy:**
- Unit: `test_entry_guard.py` — mock `quick_judge` returning `False` for
  safety; assert plan method not called. Mock returning `True`/`False` for
  intent; assert clarification response. Mock both `True`; assert pass-through.
- Unit: `test_rpc_dispatcher.py` — unknown method returns `-32601`; valid
  method dispatches; entry guard called before plan methods.
- Integration: start service in subprocess; send `{"jsonrpc":"2.0","method":"riva/ping","id":1}`
  via socket; assert `{"result":"pong"}`.

**Definition of Done for Phase 1:**
- [ ] `riva/ping` returns `pong` from the Unix socket
- [ ] Entry guard blocks adversarial and vague messages
- [ ] Schema v18 tables created on first service start
- [ ] Cairn proxy forwards `riva/*` calls transparently
- [ ] `rivaView.ts` shows split-screen (empty panels, not a stub)
- [ ] Textual TUI shows "Connected" after service starts
- [ ] Unit tests pass for entry guard and dispatcher

---

### Phase 2: Plan Engine + Contract System

**What it delivers:** The chat pane accepts a user request, runs it through
the entry guard, generates a structured plan via Ollama, and displays it as
a step-by-step card with acceptance criteria. The user can approve the plan,
assigning it to an agent and creating a contract. Plans and contracts are
persisted in the DB and visible in both UIs.

**Architecture decisions this phase:**
- `PlanEngine.decompose()` calls Ollama at temperature 0.4 with a JSON-schema
  prompt. Returns `RivaPlan` dataclass.
- `riva/plan/create` is async: returns `plan_id` immediately; Ollama runs
  in background task; client polls `riva/plan/status` until `pending_approval`.
- Plan card in the UI shows steps, acceptance criteria, risks, estimate.
  User can edit step text inline before approving.
- On approval: `ContractStore.create(plan_id, agent_id)` — agent must be
  selected before approval.

**Files to create (RIVA repo):**

| File | Purpose |
|---|---|
| `src/riva/models.py` | Dataclasses: RivaPlan, PlanStep, RivaContract |
| `src/riva/plan_engine.py` | Ollama decomposition, async background task |
| `src/riva/contract_store.py` | Contract lifecycle |
| `src/riva/rpc_handlers/plans.py` | `riva/plan/create`, `riva/plan/list`, `riva/plan/get`, `riva/plan/status`, `riva/plan/approve` |
| `src/riva/rpc_handlers/contracts.py` | `riva/contract/get`, `riva/contract/list`, `riva/contract/cancel` |
| `tests/test_plan_engine.py` | Plan engine unit tests |
| `tests/test_contract_store.py` | Contract store unit tests |

**Files to modify (Cairn repo):**

| File | Change |
|---|---|
| `apps/cairn-tauri/src/types.ts` | Add RIVA plan and contract types |
| `apps/cairn-tauri/src/rivaView.ts` | Right pane: plan card, approve flow |

**Files to modify (RIVA repo):**

| File | Change |
|---|---|
| `src/riva/rpc_dispatcher.py` | Register `plan/*` and `contract/*` methods |
| `src/riva/tui/panes/right.py` | Chat pane: plan output, approve flow |

**Dependencies:** Phase 1.

**Testing strategy:**
- Unit: `test_plan_engine.py` — mock OllamaProvider; assert output contains
  `title`, `steps` list; each step has `acceptance_criterion`. Assert malformed
  JSON triggers retry. Assert retry-failed returns graceful error.
- Unit: `test_contract_store.py` — mock DB; `create()` with valid plan_id
  inserts contract row with correct FK; non-existent plan_id raises error.

**Definition of Done for Phase 2:**
- [ ] User can type a request and see a structured plan with steps and criteria
- [ ] Plan stored in `riva_plans` and `riva_plan_steps`
- [ ] `riva/plan/create` is async (returns immediately, plan appears on poll)
- [ ] User can approve a plan, creating a `riva_contracts` row
- [ ] Plan and contract visible in both TUI and Tauri views
- [ ] Entry guard runs before `riva/plan/create`
- [ ] Malformed Ollama JSON output is retried and handled gracefully

---

### Phase 3: Agent Observatory + Properties Store

**What it delivers:** The left pane lists agents with status badges, shows
each agent's properties (CLAUDE.md, permissions) on selection, allows inline
CLAUDE.md editing, and supports new agent creation. Agents can be assigned to
contracts. The Properties Store is the source of truth — disk is derived.

**Architecture decisions this phase:**
- Left pane polls `riva/agents/list` every 3 seconds
- Agent detail panel: name, purpose, status, CLAUDE.md content, permissions,
  linked project
- CLAUDE.md editable inline — changes save to DB immediately, `synced_at`
  set to NULL until next sync
- New agent creation path: name, purpose, linked project (optional), default
  CLAUDE.md template populated from purpose
- `riva/agents/create` calls `CCManager.create_agent` (creates `cc_agents`
  row and workspace) then creates `riva_agent_properties` row

**Files to create (RIVA repo):**

| File | Purpose |
|---|---|
| `src/riva/properties_store.py` | DB-backed properties, conflict detection, sync-to-disk |
| `src/riva/rpc_handlers/agents.py` | `riva/agents/list`, `riva/agents/get`, `riva/agents/create`, `riva/agents/delete`, `riva/agents/properties/get`, `riva/agents/properties/update`, `riva/agents/properties/sync` |
| `tests/test_properties_store.py` | Properties store unit tests |

**Files to modify (Cairn repo):**

| File | Change |
|---|---|
| `apps/cairn-tauri/src/types.ts` | Add RIVA agent and properties types |
| `apps/cairn-tauri/src/rivaView.ts` | Left pane: agent list, status badges, properties panel |

**Files to modify (RIVA repo):**

| File | Change |
|---|---|
| `src/riva/rpc_dispatcher.py` | Register `agents/*` methods |
| `src/riva/tui/panes/left.py` | Agent list, properties editor (Textual) |

**Dependencies:** Phases 1 and 2.

**Testing strategy:**
- Unit: `test_properties_store.py` — update CLAUDE.md in DB; assert
  `synced_at` is NULL; call `sync_to_disk`; assert file written with correct
  content; assert `synced_at` is set.
- Conflict test: write different content to disk; call `sync_to_disk`; assert
  conflict warning is emitted before overwrite.
- Integration: create agent via `riva/agents/create`; get properties via
  `riva/agents/properties/get`; update CLAUDE.md; sync; verify file on disk.

**Definition of Done for Phase 3:**
- [ ] Left pane lists all agents with running/idle/busy status
- [ ] Clicking an agent shows its CLAUDE.md and permissions
- [ ] CLAUDE.md editable inline, saved to DB
- [ ] `sync` writes DB content to disk and commits to git
- [ ] New agent can be created from RIVA UI
- [ ] Agent can be assigned to an approved contract

---

### Phase 4: Agent Deployment + Live Stream View

**What it delivers:** The full agent lifecycle is operational. Approve contract
→ assign agent → deploy → watch live stdout stream in the observatory. This is
RIVA's core value proposition made real.

**Architecture decisions this phase:**
- Deploy: `PropertiesStore.sync_to_disk(agent_id)` then
  `CCManager.send_message(agent_id, contract_prompt)` where the prompt contains
  the plan title, step-by-step instructions, and acceptance criteria phrased
  as explicit goals
- Stream view: events rendered in left pane: `assistant_delta` as normal text;
  `tool_use` as `[tool] command` in dim; `tool_result` in dim-italic; `error`
  in red
- Tauri path: `riva/session/poll` at 200ms wraps `CCManager.poll_events`
- TUI path: `asyncio.Queue` from StreamBroker — no polling

**Files to create (RIVA repo):**

| File | Purpose |
|---|---|
| `src/riva/stream_broker.py` | Pub/sub over CCManager event list |
| `src/riva/rpc_handlers/sessions.py` | `riva/session/deploy`, `riva/session/poll`, `riva/session/stop`, `riva/session/history` |
| `tests/test_stream_broker.py` | Stream broker unit tests |

**Files to modify (Cairn repo):**

| File | Change |
|---|---|
| `apps/cairn-tauri/src/types.ts` | Add RIVA session and stream event types |
| `apps/cairn-tauri/src/rivaView.ts` | Left pane: live stream view for selected agent |

**Files to modify (RIVA repo):**

| File | Change |
|---|---|
| `src/riva/rpc_dispatcher.py` | Register `session/*` methods |
| `src/riva/tui/panes/left.py` | Live stream rendering (Textual RichLog widget) |

**Dependencies:** Phases 1, 2, 3.

**Testing strategy:**
- Unit: `test_stream_broker.py` — mock CCManager with a static event list;
  subscribe; assert queue receives events in order; assert `done` event cleans
  up the watcher.
- Integration: deploy agent via `riva/session/deploy`; poll events; assert at
  least one `assistant_delta` event within 10 seconds.
- Manual: deploy a real agent against a trivial task; verify stream appears
  in both TUI and Tauri views.

**Definition of Done for Phase 4:**
- [ ] "Deploy" button dispatches agent with contract prompt
- [ ] Agent stdout streams live in left pane
- [ ] Tool calls visually distinguished from assistant text
- [ ] "Stop" terminates the process and delivers a `done` event
- [ ] Session persisted in `riva_agent_sessions`
- [ ] Both TUI and Tauri show the same stream content

---

### Phase 5: Audit Engine + Project Management

**What it delivers:** After agent completion, RIVA audits the result against
the contract. The right pane shows an audit result card with per-criterion
pass/fail, git evidence, and overall verdict. RIVA projects are manageable
(create, list, archive) with optional Play Act linkage that surfaces Act notes
as plan context.

**Architecture decisions this phase:**
- Audit auto-triggers on `done` event when an active contract exists for the
  agent — runs in a background asyncio task, never blocks the event loop
- Audit result card in right pane: criteria table, verdict badge
  (green passed / yellow partial / red failed / grey inconclusive),
  git diff summary, files changed list
- Project list in right pane (above or below conversation, collapsible)
- Play linkage: creating a project shows an "Link to Act" dropdown populated
  from `acts` table; linked Act notes are prepended to plan generation context

**Files to create (RIVA repo):**

| File | Purpose |
|---|---|
| `src/riva/audit_engine.py` | Criterion evaluators, git subprocess, auto-trigger |
| `src/riva/play_integration.py` | Read Act data for plan context |
| `src/riva/rpc_handlers/audits.py` | `riva/audit/trigger`, `riva/audit/get`, `riva/audit/list` |
| `src/riva/rpc_handlers/projects.py` | `riva/projects/create`, `riva/projects/list`, `riva/projects/get`, `riva/projects/update`, `riva/projects/archive` |
| `tests/test_audit_engine.py` | Audit engine unit tests |

**Files to modify (Cairn repo):**

| File | Change |
|---|---|
| `apps/cairn-tauri/src/types.ts` | Add RIVA audit and project types |
| `apps/cairn-tauri/src/rivaView.ts` | Right pane: audit result card, project list |

**Files to modify (RIVA repo):**

| File | Change |
|---|---|
| `src/riva/rpc_dispatcher.py` | Register `audit/*` and `projects/*` methods |
| `src/riva/tui/panes/right.py` | Audit display, project management panel |

**Dependencies:** Phases 1-4.

**Testing strategy:**
- Unit: `test_audit_engine.py` — mock `subprocess.run` returning sample `git log`
  and `git diff` output; assert `file_exists` criterion uses `Path.exists()`;
  assert `git_contains_change` parses diff output correctly.
- Fallback test: mock git returning non-zero exit code; assert criterion is
  marked `inconclusive`, not `failed`.
- Integration: create workspace with a file; create contract with `file_exists`
  criterion for that file; run audit; assert verdict is `passed`.
- Integration: create contract with `file_exists` for a non-existent file;
  run audit; assert verdict is `failed`.

**Definition of Done for Phase 5:**
- [ ] Audit auto-triggers on agent completion when a contract is active
- [ ] Audit result card shows per-criterion pass/fail with evidence
- [ ] Overall verdict is accurate for `passed`, `partial`, `failed`, `inconclusive`
- [ ] All audits stored in `riva_audits`
- [ ] Projects CRUD works in both UIs
- [ ] Linking a project to a Play Act surfaces Act notes in plan generation

---

### Phase 6: Polish, TUI Completion, Play Write Integration

**What it delivers:** Both UIs reach production quality. The Textual TUI is a
complete standalone tool with keyboard navigation. Play write integration: when
an audit passes, RIVA proposes a Scene stage update with a user-confirm step.
Dynamic agents in the Tauri agent bar. RIVA systemd user service.

**Architecture decisions this phase:**
- Scene update proposal: passing audit triggers a notification in the right pane
  with a "Mark Scene complete?" confirm button. On confirm, calls Cairn's
  `play/scenes/update` RPC via the same proxy path. RIVA never writes Play
  state without user confirmation.
- TUI Polish: keyboard shortcuts (`?` help, `/` project filter, `Tab` pane
  focus, `Escape` deselect), project switcher panel, connection status bar.
- Dynamic agent bar: `agentBar.ts` currently has a hard-coded `CORE_AGENTS`
  array. Adding dynamic agents requires calling `riva/agents/list` at startup
  and extending `AgentId` from a union type to a string type. This touches
  `main.ts` routing logic — scope as a separate sub-phase with dedicated review.
- Systemd unit: `~/.config/systemd/user/riva.service` with
  `Environment=PATH=...` to ensure `claude` is on PATH.

**Files to modify (Cairn repo):**

| File | Change |
|---|---|
| `apps/cairn-tauri/src/agentBar.ts` | Dynamic agent list from RIVA |
| `apps/cairn-tauri/src/main.ts` | Handle dynamic agent IDs in view routing |

**Files to modify (RIVA repo):**

| File | Change |
|---|---|
| `src/riva/tui/app.py` | Full keyboard nav, help modal, project switcher, status bar |

**Files to create (RIVA repo):**

| File | Purpose |
|---|---|
| `riva.service` | systemd user service unit file |

**Dependencies:** All prior phases.

**Definition of Done for Phase 6:**
- [ ] Textual TUI is a complete standalone tool with keyboard navigation
- [ ] RIVA systemd user service starts on-demand
- [ ] Scene stage update proposed after passing audit; user confirmation required
- [ ] Dynamic user-created agents appear in Tauri agent bar
- [ ] Dark theme consistent with Cairn throughout both UIs
- [ ] Complete workflow (request → plan → contract → deploy → stream → audit → verdict) works end-to-end

---

## Files Affected — Complete List

### New Files to Create (RIVA repo)

```
src/riva/service.py                 Phase 1
src/riva/rpc_dispatcher.py          Phase 1
src/riva/entry_guard.py             Phase 1
src/riva/db.py                      Phase 1
src/riva/schema.py                  Phase 1
src/riva/errors.py                  Phase 1
src/riva/models.py                  Phase 2
src/riva/plan_engine.py             Phase 2
src/riva/contract_store.py          Phase 2
src/riva/properties_store.py        Phase 3
src/riva/stream_broker.py           Phase 4
src/riva/audit_engine.py            Phase 5
src/riva/play_integration.py        Phase 5
src/riva/rpc_handlers/__init__.py   Phase 1
src/riva/rpc_handlers/system.py     Phase 1
src/riva/rpc_handlers/plans.py      Phase 2
src/riva/rpc_handlers/contracts.py  Phase 2
src/riva/rpc_handlers/agents.py     Phase 3
src/riva/rpc_handlers/sessions.py   Phase 4
src/riva/rpc_handlers/audits.py     Phase 5
src/riva/rpc_handlers/projects.py   Phase 5
src/riva/tui/__init__.py            Phase 1
src/riva/tui/app.py                 Phase 1 (stub), Phase 6 (complete)
src/riva/tui/panes/__init__.py      Phase 1
src/riva/tui/panes/left.py          Phase 1 (stub), Phases 3-4 (functional)
src/riva/tui/panes/right.py         Phase 1 (stub), Phases 2+5 (functional)
tests/__init__.py                   Phase 1
tests/test_entry_guard.py           Phase 1
tests/test_rpc_dispatcher.py        Phase 1
tests/test_plan_engine.py           Phase 2
tests/test_contract_store.py        Phase 2
tests/test_properties_store.py      Phase 3
tests/test_stream_broker.py         Phase 4
tests/test_audit_engine.py          Phase 5
riva.service                        Phase 6
```

### Files to Modify (RIVA repo)

```
pyproject.toml                      Phase 1: add textual, trcore; add CLI entry point
```

### New Files to Create (Cairn repo)

```
src/cairn/rpc_handlers/riva.py      Phase 1: Unix socket proxy handler
```

### Files to Modify (Cairn repo)

```
src/cairn/ui_rpc_server.py          Phase 1: register riva/* namespace
src/cairn/play_db.py                Phase 1: add v18 migration block
apps/cairn-tauri/src/rivaView.ts    All phases (evolves each phase)
apps/cairn-tauri/src/types.ts       Phases 2, 3, 4, 5: new RIVA types per phase
apps/cairn-tauri/src/agentBar.ts    Phase 6: dynamic agents
apps/cairn-tauri/src/main.ts        Phase 6: dynamic agent routing
```

### Files to Leave Untouched

```
Cairn: src/cairn/services/cc_manager.py         Used as-is (RIVA imports it)
Cairn: src/cairn/services/cc_session_observer.py Used as-is
Cairn: src/cairn/rpc_handlers/cc.py             Used as-is (Helm/cc views continue)
Cairn: apps/cairn-tauri/src/cairnView.ts        Reference only
Cairn: apps/cairn-tauri/src/reosView.ts         Reference only
trcore: all files                               Used as dependencies, not modified
```

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cairn's `ui_rpc_server.py` dispatch table cannot handle async proxy calls | Medium | High | The existing `handle_cc_session_send` is already `async`, suggesting the dispatcher supports async handlers — but verify before Phase 1 begins. |
| RIVA socket not running when Cairn proxy is called | High | Medium | Proxy returns `{"code": -32099, "message": "RIVA service not running"}`. `rivaView.ts` shows "RIVA offline" state with a "Start RIVA" button. |
| Ollama plan decomposition produces malformed JSON | High | Medium | Wrap in retry-with-repair: parse JSON; on failure, send a repair prompt ("fix this JSON"); fall back to error after 2 retries. |
| CCManager `_procs` dict is per-process. RIVA's CCManager is separate from Cairn's. | High | Medium | RIVA agents are distinct from Cairn/Helm cc_agents by convention (RIVA agents created via `riva/agents/create` are distinguished by having `riva_agent_properties` rows). Both share `talkingrock.db`; `_procs` is ephemeral and per-process by design. Document this clearly. |
| Schema migration races: Cairn and RIVA both start and both try to run v18 migration | Low | High | RIVA migration is idempotent (`CREATE TABLE IF NOT EXISTS`). The `PRAGMA user_version` check in `play_db.py` uses an exclusive transaction. Idempotency makes double-run harmless in practice. |
| `git` not present in agent workspace at audit time | Medium | Medium | CCManager already runs `git init` on workspace creation. Audit engine verifies git is initialized; falls back to file-system-only checks with `inconclusive` git evidence flag. |
| LLM plan generation blocks RPC thread | High | Medium | `riva/plan/create` is async: returns `plan_id` immediately; Ollama runs in background task; client polls `riva/plan/status`. Addressed by design. |
| `sync_to_disk` overwrites user's manual CLAUDE.md edits | Medium | Medium | Before overwriting, compare disk hash to last-synced hash. If different, emit a conflict warning in the UI and require explicit user confirmation. |
| Audit criterion "tests pass" is unverifiable without executing tests | High | Low | Mark as `manual_verification` in all Phases 1-5. Audit report flags these for user attention. Automated test execution is Phase 6+ consideration. |
| `rivaView.ts` dynamic agent bar changes break `main.ts` routing | Medium | High | Scoped to Phase 6 only. Treated as a separate sub-phase with dedicated review before merging. Keep `CORE_AGENTS` hard-coded through Phase 5. |
| RIVA's asyncio event loop conflicts with Textual's internal event loop | Medium | High | Textual has its own asyncio runner. If RIVA service runs inline with the TUI, they must share one loop via `asyncio.get_event_loop()`. Evaluate at Phase 1: may need RIVA service to run in a background thread with its own loop, communicating via `run_coroutine_threadsafe`. |
| `claude` binary not on PATH in RIVA systemd service environment | Medium | Medium | The systemd user service unit must specify `Environment=PATH=...` explicitly. Document this as a known gotcha. |
| Two CLAUDE.md contexts conflict: per-agent CLAUDE.md vs global `~/.claude/CLAUDE.md` | Low | Medium | Claude Code loads both (project + user). RIVA's per-agent CLAUDE.md is additive — it must not override global safety constraints. The RIVA CLAUDE.md template must document this relationship explicitly. |

---

## Testing Strategy

### Overall Approach

Unit tests for all subsystems with mocked dependencies (Ollama, CCManager,
filesystem, subprocess). Integration tests via the Unix socket with a real
running service. Manual testing for both UIs against real Claude Code agents.
Test files live in `RIVA/tests/`.

### Phase-Level Details

**Phase 1**
- `test_entry_guard.py`: three paths — safety blocked, intent blocked,
  pass-through. Mock `quick_judge` for each.
- `test_rpc_dispatcher.py`: unknown method → `-32601`; known method dispatches
  correctly; plan methods trigger entry guard.
- Socket integration: subprocess start, `riva/ping`, assert `pong`.

**Phase 2**
- `test_plan_engine.py`: mock OllamaProvider; well-formed output assertions;
  malformed JSON retry logic; retry-failed graceful error.
- `test_contract_store.py`: mock DB via `unittest.mock`; create contract;
  assert row contents; non-existent plan_id raises.

**Phase 3**
- `test_properties_store.py`: update CLAUDE.md; assert `synced_at = NULL`;
  sync; assert file content matches DB; assert `synced_at` set; conflict
  detection on differing disk content.

**Phase 4**
- `test_stream_broker.py`: mock CCManager with static events list; subscribe;
  advance mock events; assert queue receives new events; `done` cleans up.

**Phase 5**
- `test_audit_engine.py`: mock `subprocess.run`; sample git output; assert
  criterion evaluation logic per type. Non-zero exit → `inconclusive`.
- Integration audit tests: create workspace, create contract, run audit,
  assert verdict.

**Phase 6**
- Manual end-to-end: full workflow from natural language request through
  audit verdict, verified in both TUI and Tauri.

---

## Definition of Done

RIVA is complete when all of the following are true:

### Core Functionality
- [ ] User can type a natural language request and receive a structured plan
      with acceptance criteria
- [ ] Plan can be approved to create a contract
- [ ] Agent can be assigned to the contract and deployed from the RIVA UI
- [ ] Agent stdout streams live in the observatory pane
- [ ] After completion, RIVA audits output against the contract's criteria
- [ ] Audit shows per-criterion pass/fail with git evidence and overall verdict
- [ ] Agent properties (CLAUDE.md, permissions) are editable and synced to disk

### Data Integrity
- [ ] All RIVA state persists in `talkingrock.db` across service restarts
- [ ] Agent event streams persist in `cc_history`
- [ ] Audit results persist in `riva_audits`
- [ ] CLAUDE.md on disk reflects DB state after sync

### Safety
- [ ] Entry guard runs before every plan creation request
- [ ] Adversarial messages return a boundary response, never enter plan engine
- [ ] Vague messages prompt for clarification
- [ ] Disk sync conflict detection prevents silent overwrites

### Integration
- [ ] Textual TUI and Tauri rivaView display consistent data from the same backend
- [ ] Cairn `riva/*` RPC proxy works transparently
- [ ] RIVA socket offline state handled gracefully in both UIs
- [ ] Play Act linkage surfaces Act notes as plan generation context

### Quality
- [ ] All core subsystems have unit tests
- [ ] `ruff check src/` passes with no errors
- [ ] `mypy src/` passes with no errors
- [ ] `pytest tests/` passes with no failures
- [ ] Dark theme consistent with Cairn throughout

---

## Confidence Assessment

**High confidence:** Phase 1 (service scaffolding, entry guard, schema). The
patterns are fully established — Unix socket server, `quick_judge`, SQLite
migration. Low unknowns.

**High confidence:** Phase 2 (plan engine). Ollama structured output is proven
in Cairn's existing code. The JSON schema for plans is straightforward. The
main risk is malformed output — mitigated by retry logic, which is a known
pattern.

**High confidence:** Phase 3 (properties store). DB-backed file sync is a
simple pattern. The conflict detection via hash comparison is a well-understood
technique.

**Medium confidence:** Phase 4 (live streaming). CCManager is proven; the
Stream Broker is new code. The Textual TUI's asyncio integration with the
RIVA service event loop needs careful design at Phase 1 — the asyncio / Textual
loop interaction is the single highest-risk technical unknown. The Tauri
polling path is low-risk (same pattern as `cc/session/poll`).

**Medium confidence:** Phase 5 (audit engine). Git subprocess integration is
straightforward. The main risk is in defining what counts as "evidence" for
each criterion type, and setting accurate user expectations for what audit can
and cannot verify automatically. The `manual_verification` escape hatch keeps
this from becoming a blocker.

**Low-medium confidence:** Phase 6 (dynamic agent bar, Play write, systemd).
The dynamic agent bar touches `main.ts` routing — the highest-risk file in
the Tauri frontend. The Textual TUI completion work is proportional but
wide-ranging. Budget extra time for Phase 6.

---

## Unknowns Requiring Validation Before Phase 1

1. **Cairn `ui_rpc_server.py` async proxy support.** Verify the dispatcher's
   event loop can handle an `async` proxy handler that opens a Unix socket
   connection. Evidence that it can: `handle_cc_session_send` in
   `src/cairn/rpc_handlers/cc.py` is already `async`. Validate before writing
   the proxy handler.

2. **Schema migration coordination.** If Cairn and RIVA start concurrently
   (possible but unlikely), both may attempt the v18 migration simultaneously.
   The existing `play_db.py` migration uses `PRAGMA user_version` with an
   exclusive transaction. Confirm RIVA calls the same migration path (not a
   separate one) and that the `IF NOT EXISTS` guards make double-run harmless.

3. **`trcore.db.get_db()` path resolution from RIVA process.** Verify that
   `trcore.db.get_db()` returns `~/.talkingrock/talkingrock.db` when called
   from RIVA's process. The path depends on `TALKINGROCK_DATA_DIR` env var
   and `settings.data_dir`. RIVA's service startup must set this env var if
   the default differs from Cairn's.

4. **Textual TUI asyncio loop sharing.** Textual runs its own asyncio event
   loop. If RIVA service runs inline with the TUI, they must share one loop.
   Evaluate at Phase 1: the clean solution may be to run the RIVA service in
   a background thread with `asyncio.run()` in that thread, communicating with
   the Textual app via `asyncio.run_coroutine_threadsafe`. Decide and document
   this as an ADR before TUI development begins.

5. **`claude` binary PATH in the RIVA service environment.** When RIVA dispatches
   agents via CCManager, `claude` must be on PATH. Verify the RIVA service
   (started by systemd or by the TUI) inherits a PATH that includes the `claude`
   binary location. Systemd user services may not inherit the login shell's PATH.
   The systemd unit file must specify `Environment=PATH=...` explicitly.
