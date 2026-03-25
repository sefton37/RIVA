# RIVA Documentation

Complete technical reference for understanding and building on RIVA.

## Quick Navigation

**New to RIVA?** Start here in order:

1. **[API_REFERENCE.md](API_REFERENCE.md)** — Complete RPC method reference for the Tauri frontend
   - JSON-RPC 2.0 protocol format
   - All 25 methods with params, responses, and error codes
   - Data model relationships (ERD)
   - Typical workflows
   - Tauri implementation tips

2. **[INTERNAL_ARCHITECTURE.md](INTERNAL_ARCHITECTURE.md)** — Code map for developers
   - Directory structure and file purposes
   - Data flow for creating and executing contracts
   - RPC dispatcher pattern
   - CCManager interface (from trcore)
   - Database schema with 7 RIVA tables + 2 from trcore
   - Testing strategy and common extensions

3. **[TAURI_INTEGRATION_GUIDE.md](TAURI_INTEGRATION_GUIDE.md)** — Build a Tauri frontend
   - Rust RPC client via Unix socket
   - Tauri command registration
   - TypeScript bindings and types
   - UI state management patterns (Svelte example)
   - Component examples (ProjectList, SessionMonitor)
   - Error handling including entry guard rejection
   - Local testing and deployment checklist

4. **[ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md)** — High-level design doc
   - System phases (1-6)
   - Key files and their roles
   - Running the service and TUI
   - NOL integration
   - Conventions

---

## Core Concepts

### The RIVA Pipeline

```
1. User Request (Plan)
   ↓
2. Entry Guard (Safety + Intent Check)
   ↓
3. Plan Decomposition (Ollama)
   ↓
4. User Approval
   ↓
5. Contract Creation (NOL Assembly)
   ↓
6. Agent Deployment (Claude Code Process)
   ↓
7. Live Streaming (Events)
   ↓
8. Auto-Audit (Verification Criteria)
   ↓
9. Scene Update (Play Integration)
```

### Key Tables

| Table | Purpose | Rows |
|-------|---------|------|
| `riva_projects` | Work containers | Users create these |
| `riva_plans` | Ollama-decomposed plans | From user requests |
| `riva_plan_steps` | Individual steps | ~5-10 per plan |
| `riva_contracts` | Approved, enforceable plans | 1:1 with plans |
| `riva_audits` | Post-completion verification | Multiple per contract |
| `riva_agent_properties` | DB-backed agent config | 1 per agent |
| `riva_agent_sessions` | Session audit trail | Many per agent |
| `cc_agents` (trcore) | Agent metadata | Users create these |
| `cc_history` (trcore) | Persistent messages | Thousands per agent |

### Key Classes

| Class | Location | Role |
|-------|----------|------|
| `RivaPlan` | models.py | Plan dataclass with steps |
| `RivaContract` | models.py | Contract with verification criteria |
| `VerificationCriterion` | models.py | 5 types: file_exists, function_defined, git_contains_change, git_commit_message, manual_verification |
| `CCManager` | trcore/cc_manager.py | Agent process lifecycle (external) |
| `PlanEngine` | plan_engine.py | Ollama decomposition |
| `StreamBroker` | stream_broker.py | Pub/sub over CCManager events (TUI only) |
| `RivaCCDatabase` | cc_adapter.py | CCDatabase protocol adapter |

---

## RPC Method Map

### System (2 methods)
- `riva/ping` — Health check
- `riva/status` — Uptime + version

### Plans (5 methods)
- `riva/plan/create` — Start async decomposition (guarded)
- `riva/plan/status` — Poll decomposition progress
- `riva/plan/get` — Get full plan with steps
- `riva/plan/list` — List plans for a project
- `riva/plan/approve` — Approve and create contract

### Contracts (3 methods)
- `riva/contract/get` — Get contract details
- `riva/contract/list` — List contracts
- `riva/contract/cancel` — Cancel contract

### Agents (7 methods)
- `riva/agents/list` — List agents
- `riva/agents/get` — Get agent details + properties
- `riva/agents/create` — Create agent with workspace
- `riva/agents/delete` — Delete agent
- `riva/agents/properties/get` — Get CLAUDE.md + permissions
- `riva/agents/properties/update` — Update properties (DB only)
- `riva/agents/properties/sync` — Sync DB to disk

### Sessions (4 methods)
- `riva/session/deploy` — Dispatch agent with contract
- `riva/session/poll` — Poll agent stream events
- `riva/session/stop` — Stop running agent
- `riva/session/history` — Get conversation history

### Audits (3 methods)
- `riva/audit/trigger` — Manually trigger audit
- `riva/audit/get` — Get audit result
- `riva/audit/list` — List audits

### Projects (5 methods)
- `riva/projects/create` — Create project
- `riva/projects/list` — List projects
- `riva/projects/get` — Get project details
- `riva/projects/update` — Update project
- `riva/projects/archive` — Archive project

---

## File Purposes Quick Reference

**Core:**
- `service.py` — Unix socket server + handler registration
- `rpc_dispatcher.py` — JSON-RPC dispatch + entry guard interception
- `db.py` — SQLite access
- `schema.py` — Table migrations

**Planning & Contracts:**
- `plan_engine.py` — Ollama decomposition
- `contract_store.py` — Plan → Contract with criteria parsing
- `nol_contract.py` — NOL assembly + intent hashing

**Agents & Sessions:**
- `cc_adapter.py` — CCDatabase protocol for trcore.CCManager
- `properties_store.py` — DB-backed config + disk sync
- `stream_broker.py` — Pub/sub for TUI (not used by Tauri)

**Verification:**
- `audit_engine.py` — Post-completion verification
- `play_integration.py` — Read Act data
- `play_write.py` — Propose scene updates (Phase 6)

**RPC Handlers:**
- `rpc_handlers/system.py` — ping, status
- `rpc_handlers/plans.py` — plan CRUD
- `rpc_handlers/contracts.py` — contract CRUD
- `rpc_handlers/agents.py` — agent CRUD + properties
- `rpc_handlers/sessions.py` — deploy, poll, stop, history
- `rpc_handlers/audits.py` — audit trigger + list
- `rpc_handlers/projects.py` — project CRUD

---

## Key Implementation Details

### Entry Guard
- Screens `user_request` before plan creation
- Checks for safety (exfiltration, injection) and intent alignment
- Returns code -32001 with guard_type ("safety" or "intent") and reason

### Plan Decomposition
- Async call to Ollama via PlanEngine
- Returns plan_id immediately
- Poll `riva/plan/status` to track progress
- Status: "draft" → "decomposing" → "ready"

### Verification Criteria (5 types)
1. **file_exists** — Path must exist in agent workspace
2. **function_defined** — Function name must be grep-able in file
3. **git_contains_change** — Path must appear in git diff
4. **git_commit_message** — Keyword must appear in git log
5. **manual_verification** — Always "inconclusive", requires human review

### Session Polling Pattern
1. Call `riva/session/poll(agent_id, since=0)` to get all buffered events
2. Save `next_index` from response
3. Call again with `since=<next_index>` to get only new events
4. Stop when you receive `type="done"` event or `busy=false && events.length=0`

### NOL Assembly
- Each verification criterion becomes a POST condition comment
- Intent hash (SHA-256) makes contracts content-addressable
- Optional structural verification via nolang binary (env var NOLANG_BINARY)

---

## Common Tasks

### Add a New RPC Method
1. Create handler in `rpc_handlers/new_domain.py`
2. Register in `service.py :: _register_phaseX_handlers()`
3. Add tests in `tests/test_new_domain.py`

### Add a New Verification Criterion Type
1. Update `models.py :: VerificationCriterion`
2. Update `contract_store.py :: _parse_criterion_from_text()`
3. Update `audit_engine.py :: verify_new_type()`

### Extend Agent Properties
1. Add field to `riva_agent_properties` table (schema.py)
2. Update `properties_store.py` getters/setters
3. Update sync logic to read/write the field

---

## Testing

**Run all tests:**
```bash
cd /home/kellogg/dev/RIVA
.venv/bin/pytest tests/ -x --tb=short -q
```

**Test RPC directly (without socket):**
```python
from riva.rpc_dispatcher import dispatch

request = '{"jsonrpc": "2.0", "method": "riva/ping", "id": 1}'
response = dispatch(request)
print(response)
```

---

## Deployment

**Start RIVA service:**
```bash
riva-service
# or
systemctl --user start riva
```

**Verify socket exists:**
```bash
ls -la ~/.talkingrock/riva.sock
```

**Check Ollama is running (for plan decomposition):**
```bash
curl http://localhost:11434/api/tags
```

**Verify claude CLI is in PATH:**
```bash
which claude
claude --help
```

---

## Performance Notes

- **Event buffer:** Capped at 10,000 entries per agent (old events pruned)
- **History queries:** Use limit parameter (default 100)
- **Plan decomposition:** Async, may take 5-30 seconds depending on Ollama
- **Audit trigger:** Can be slow (git operations on large repos)
- **Polling interval:** Start at 200ms, exponential backoff to 2s
- **Session resumption:** Claude supports --resume for multi-turn sessions

---

## Known Limitations

- CCManager not transaction-aware (race conditions on concurrent sends)
- Event buffer pruning at 10K entries loses older context
- Plan decomposition blocks RPC dispatch while calling Ollama
- No rate limiting on RPC calls
- Agent properties sync not atomic (TOCTOU race)
- Manual verification criteria require external UI acknowledgment

---

## File Inventory

**By location:**

```
src/riva/
├── service.py (187 lines)
├── rpc_dispatcher.py (168 lines)
├── entry_guard.py (TBD)
├── plan_engine.py (TBD)
├── contract_store.py (120+ lines)
├── nol_contract.py (TBD)
├── audit_engine.py (TBD)
├── properties_store.py (TBD)
├── stream_broker.py (143 lines)
├── play_integration.py (TBD)
├── play_write.py (TBD)
├── cc_adapter.py (26 lines)
├── models.py (150+ lines)
├── schema.py (136 lines)
├── db.py (TBD)
├── errors.py (53 lines)
├── rpc_handlers/
│   ├── system.py (37 lines)
│   ├── plans.py (106 lines)
│   ├── contracts.py (47 lines)
│   ├── agents.py (184 lines)
│   ├── sessions.py (252 lines)
│   ├── audits.py (75 lines)
│   └── projects.py (182 lines)
└── tui/ (TBD)
```

---

## Related Projects

- **trcore** (`/home/kellogg/dev/talkingrock-core/`) — Shared infrastructure (CCManager, atomic ops)
- **Cairn** (`/home/kellogg/dev/Cairn/`) — Parent project (attention minding, uses RIVA)
- **Play** (Cairn subsystem) — Scene/act management (RIVA integrates via play_integration.py)

---

## Questions?

Refer to the detailed documentation:
- **API details:** See `API_REFERENCE.md`
- **Code structure:** See `INTERNAL_ARCHITECTURE.md`
- **Building UI:** See `TAURI_INTEGRATION_GUIDE.md`
- **High-level design:** See `ARCHITECTURE_PLAN.md`

