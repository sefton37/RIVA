# RIVA Internal Architecture — Code Map for Developers

This document maps RIVA's internal structure for developers building on or modifying the system.

---

## Directory Structure

```
src/riva/
├── service.py                    # Unix socket server, handler registration by phase
├── rpc_dispatcher.py             # JSON-RPC 2.0 dispatch, entry guard interception
├── entry_guard.py                # Safety + intent screening (quick_judge from trcore)
├── plan_engine.py                # Ollama-based plan decomposition (async)
├── contract_store.py             # Plan → Contract with verification criteria parsing
├── nol_contract.py               # NOL assembly generation + intent hashing
├── audit_engine.py               # Post-completion verification (5 criterion types)
├── properties_store.py           # DB-backed agent properties, disk sync
├── stream_broker.py              # Pub/sub layer over CCManager event lists
├── play_integration.py           # Read Act data for plan context
├── play_write.py                 # Scene update proposals after audits pass
├── cc_adapter.py                 # CCDatabase protocol for trcore.CCManager
├── models.py                     # Dataclasses: RivaPlan, RivaContract, etc.
├── schema.py                     # DB migrations: 7 riva_* tables
├── db.py                         # Database access wrapper
├── errors.py                     # Error hierarchy
├── rpc_handlers/
│   ├── system.py                 # riva/ping, riva/status
│   ├── plans.py                  # riva/plan/* (create, status, get, list, approve)
│   ├── contracts.py              # riva/contract/* (get, list, cancel)
│   ├── agents.py                 # riva/agents/*, riva/agents/properties/*
│   ├── sessions.py               # riva/session/* (deploy, poll, stop, history)
│   ├── audits.py                 # riva/audit/* (trigger, get, list)
│   ├── projects.py               # riva/projects/* (create, list, get, update, archive)
│   └── __init__.py
└── tui/
    ├── app.py                    # Main Textual TUI, keyboard nav
    └── panes/
        ├── left.py               # Observatory (agent list, stream)
        └── right.py              # Chat (plans, audits)
```

---

## Data Flow: Creating and Executing a Contract

### 1. Project Creation

**File:** `rpc_handlers/projects.py :: handle_projects_create()`

```
Input: { name, description, act_id? }
  ↓
UUID generation (proj-{12-char-hex})
  ↓
Insert into riva_projects table
  ↓
Output: { id, name, status: "active", created_at }
```

---

### 2. Plan Decomposition

**File:** `rpc_dispatcher.py :: dispatch()` → Entry Guard → `rpc_handlers/plans.py :: handle_plan_create()`

```
Input: { project_id, user_request }
  ↓
[GUARDED] entry_guard.guard_or_raise(provider, user_request)
  ├─ Safety check: exfiltration, injection, malicious
  ├─ Intent check: alignment with user's goals
  └─ Throws EntryGuardBlocked (code -32001) if rejected
  ↓
plan_engine.decompose(project_id, user_request)
  ├─ Async Ollama call via PlanEngine
  ├─ Generates: title, steps, risks, estimated_minutes
  ├─ Stores in riva_plans + riva_plan_steps
  └─ Status: "draft" → "decomposing" → "ready"
  ↓
Output: { plan_id, status: "draft" }
```

**Files:**
- `plan_engine.py :: PlanEngine.decompose()` — Ollama call, JSON parsing
- `schema.py` — riva_plans, riva_plan_steps tables
- `models.py :: RivaPlan, PlanStep` — Dataclasses

---

### 3. Plan Polling & Approval

**Files:**
- `rpc_handlers/plans.py :: handle_plan_status()` — Poll decomposition
- `rpc_handlers/plans.py :: handle_plan_approve()` — Trigger contract creation

```
Status Polling:
  Input: { plan_id }
    ↓
  SELECT * FROM riva_plans WHERE id=?
    ↓
  Output: RivaPlan.to_dict() with status, steps, risks
  
Plan Approval:
  Input: { plan_id, agent_id }
    ↓
  contract_store.create_contract(plan_id, agent_id)
    ├─ Fetch plan + steps
    ├─ Parse acceptance_criterion → VerificationCriterion (5 types)
    ├─ nol_contract.create_nol_contract()
    │  ├─ Generate NOL assembly with POST conditions
    │  ├─ Hash intent (SHA-256)
    │  └─ (Optional) Verify via nolang binary
    ├─ INSERT into riva_contracts
    └─ Return RivaContract.to_dict()
  ↓
  Output: Contract with verification_criteria, nol_assembly, status: "active"
```

**Files:**
- `contract_store.py :: create_contract()` — Contract factory
- `contract_store.py :: _parse_criterion_from_text()` — Criterion heuristic parsing
- `nol_contract.py` — NOL assembly + intent hashing
- `models.py :: VerificationCriterion` — 5 types: file_exists, function_defined, git_contains_change, git_commit_message, manual_verification

---

### 4. Agent Management

**File:** `rpc_handlers/agents.py`

```
Agent Creation:
  Input: { name, purpose }
    ↓
  ccmanager.create_agent(username, name, purpose)
    ├─ Slug = slugify(name)
    ├─ UUID = uuid4().hex
    ├─ cwd = WORKSPACE_ROOT / slug
    ├─ INSERT into cc_agents table (trcore schema)
    └─ _setup_workspace(name, slug, purpose, cwd)
       ├─ mkdir(cwd)
       ├─ Write CLAUDE.md, README.md, .gitignore
       └─ git init + git commit
    ↓
  properties_store.create_properties(agent_id, name, purpose)
    └─ INSERT into riva_agent_properties (DB as source of truth)
    ↓
  Output: { id, name, slug, purpose, cwd }

Properties Management:
  Get: properties_store.get_properties(agent_id)
       → SELECT from riva_agent_properties
  
  Update: properties_store.update_claude_md(agent_id, content)
          + properties_store.update_permissions(agent_id, perms)
          → UPDATE riva_agent_properties, set synced_at=NULL
  
  Sync: properties_store.sync_to_disk(agent_id, cwd)
        ├─ Write claude_md → cwd/CLAUDE.md
        ├─ Write permissions_json → cwd/.claude/permissions.json
        └─ UPDATE synced_at = now()
```

**Files:**
- `rpc_handlers/agents.py :: handle_agents_create()` — Agent factory
- `properties_store.py` — DB-backed config, disk sync
- `cc_adapter.py :: RivaCCDatabase` — Protocol adapter for trcore.CCManager
- `trcore/cc_manager.py :: CCManager.create_agent()` — Workspace setup

---

### 5. Session Deployment & Streaming

**File:** `rpc_handlers/sessions.py`

```
Session Deploy:
  Input: { contract_id, agent_id }
    ↓
  handle_session_deploy()
    ├─ Fetch agent from ccmanager.list_agents()
    ├─ properties_store.get_properties() + sync_to_disk()
    │  └─ Ensure CLAUDE.md is current
    ├─ _build_dispatch_prompt(contract_id)
    │  ├─ Fetch plan title, steps, acceptance criteria
    │  └─ Format as markdown task description
    ├─ session_id = f"sess-{uuid4().hex[:12]}"
    ├─ INSERT into riva_agent_sessions
    └─ await ccmanager.send_message(agent_id, prompt)
       └─ Spawns: claude --print --output-format stream-json --verbose ...
    ↓
  Output: { session_id, agent_id, contract_id, status: "deployed" }

Session Polling:
  Input: { agent_id, since: int }
    ↓
  ccmanager.poll_events(agent_id, since)
    ├─ Fetch AgentProcess from _procs[agent_id]
    ├─ Return events[since:] (non-blocking slice)
    ├─ Prune buffer if > 10,000 entries
    └─ Return { events, next_index, busy }
    ↓
  Event Types:
    - "user": Initial prompt
    - "assistant_delta": Incremental text (accumulate these)
    - "tool_use": { tool, input } e.g., Read src/main.ts
    - "tool_result": { text, is_error }
    - "error": { text }
    - "done": Session complete
    ↓
  Output: { events: [], next_index: 8, busy: false }

Session Stop:
  Input: { agent_id }
    ↓
  await ccmanager.stop_session(agent_id)
    ├─ proc.terminate() (SIGTERM)
    ├─ Wait 5s for clean exit
    ├─ proc.kill() (SIGKILL) if needed
    └─ Set busy=False, append "done" event
    ↓
  UPDATE riva_agent_sessions SET status="stopped" WHERE agent_id=? AND status="running"
    ↓
  Output: { ok: true }
```

**Files:**
- `rpc_handlers/sessions.py :: handle_session_deploy()` — Deployment factory
- `rpc_handlers/sessions.py :: _build_dispatch_prompt()` — Prompt assembly
- `trcore/cc_manager.py :: send_message()` — Process spawning + streaming
- `trcore/cc_manager.py :: _read_output()` — Stream-json parsing

---

### 6. Streaming & Background Tasks

**File:** `stream_broker.py`

The StreamBroker provides pub/sub over CCManager event lists for the Textual TUI:

```
Usage (TUI only, not used by Tauri):
  broker.subscribe(agent_id)
    └─ Returns asyncio.Queue
  
  async for event in queue:
    # Process event as it arrives
  
  broker.on_agent_done(callback)
    └─ Auto-triggers audit on session completion
```

For Tauri: Just use `riva/session/poll` directly. No broker needed.

---

### 7. Audit Verification

**File:** `rpc_handlers/audits.py` → `audit_engine.py`

```
Audit Trigger:
  Input: { contract_id }
    ↓
  run_audit(contract_id, cwd, triggered_by="user")
    ├─ Fetch contract + criteria
    ├─ git diff [agent cwd]
    ├─ For each criterion:
    │  ├─ file_exists: os.path.exists(path)
    │  ├─ function_defined: grep(file, name)
    │  ├─ git_contains_change: "path" in git diff
    │  ├─ git_commit_message: "keyword" in git log
    │  └─ manual_verification: always "inconclusive"
    ├─ overall_verdict = all passed? "passed" : (any failed? "failed" : "inconclusive")
    ├─ INSERT into riva_audits
    └─ Return audit result
    ↓
  Output: { id, contract_id, criteria_results_json, overall_verdict, audited_at }

Auto-Audit Flow (via StreamBroker):
  broker.on_agent_done(agent_id)
    ├─ list_contracts(status="active")
    ├─ Find contract where agent_id matches
    └─ run_audit(contract_id, ...)
       └─ On "passed" verdict: play_write.propose_scene_update(...)
```

**Files:**
- `audit_engine.py :: run_audit()` — Verification orchestrator
- `audit_engine.py :: verify_*()` — Individual criterion checkers
- `play_write.py` — Phase 6: Scene update proposals

---

## RPC Dispatcher Pattern

**File:** `rpc_dispatcher.py`

```python
# Global dispatch table
_DISPATCH: dict[str, Callable] = {
    "riva/ping": lambda **_kw: handle_ping(),
    "riva/plan/create": handle_plan_create,
    "riva/contract/get": handle_contract_get,
    ...
}

# Guarded methods (entry guard runs before handler)
_GUARDED_METHODS: set[str] = {
    "riva/plan/create",
}

def dispatch(raw: str, provider=None) -> str:
    """Main dispatcher. Called once per client request."""
    try:
        request = json.loads(raw)
    except:
        return json.dumps(_make_response(None, error=_make_error(PARSE_ERROR, ...)))
    
    method = request.get("method")
    params = request.get("params", {})
    req_id = request.get("id")
    
    # Entry guard for guarded methods
    if method in _GUARDED_METHODS and provider:
        user_message = params.get("user_request") or params.get("message", "")
        try:
            guard_or_raise(provider, user_message)
        except Exception as exc:
            return json.dumps(_make_response(req_id, error=_make_error(ENTRY_GUARD_BLOCKED, ...)))
    
    # Dispatch handler
    handler = _DISPATCH.get(method)
    try:
        result = handler(**params)
        return json.dumps(_make_response(req_id, result=result))
    except Exception as exc:
        return json.dumps(_make_response(req_id, error=_make_error(INTERNAL_ERROR, ...)))
```

**Key Points:**
- All handlers are registered during service initialization by phase (Phase 2, 3, 4, 5)
- Entry guard runs before any guarded method (currently only `riva/plan/create`)
- Handlers receive `**params` as kwargs; must handle missing params gracefully
- All exceptions are caught and returned as JSON-RPC errors

---

## Entry Guard: Safety & Intent

**File:** `entry_guard.py`

```python
def guard_or_raise(provider: LLMProvider, user_message: str) -> None:
    """Screen user message for safety + intent alignment.
    
    Raises EntryGuardBlocked if rejected.
    """
    # Uses trcore.quick_judge for rapid checks
    # 1. Safety: exfiltration, injection, malicious patterns
    # 2. Intent: does request align with project scope?
    
    # Error includes guard_type ("safety" or "intent") + reason
```

**Integration:**
- Called only for `riva/plan/create` (in rpc_dispatcher)
- Skipped if provider=None (testing path)
- Returns -32001 error with `data` field containing guard_type and reason

---

## Session Initialization (Service Startup)

**File:** `service.py :: async def run_service()`

```python
async def run_service(port=None, *, provider=None) -> None:
    # 1. Ensure schema
    ensure_schema()
    
    # 2. Set start time for uptime tracking
    set_start_time()
    
    # 3. Register Phase 2 handlers
    _register_phase2_handlers(provider=provider)
    
    # 4. Register Phase 3 handlers (returns CCManager)
    manager = _register_phase3_handlers()
    
    # 5. Register Phase 4 handlers (returns StreamBroker)
    broker = _register_phase4_handlers(manager)
    
    # 6. Register Phase 5 handlers (audits + projects)
    _register_phase5_handlers(manager, broker)
    
    # 7. Start Unix domain socket server
    socket_path = get_socket_path()
    server = await asyncio.start_unix_server(
        lambda r, w: _handle_client(r, w, provider=provider),
        path=socket_path
    )
    
    # 8. Run forever
    async with server:
        await server.serve_forever()
```

**Key Architecture Decisions:**
- All handler registration happens at startup (phase gates)
- CCManager is shared between Phase 3, 4, 5 (via set_manager)
- StreamBroker is shared between Phase 4, 5 (via set_broker)
- Entry guard provider is optional (for testing without Ollama)

---

## Database Schema

**File:** `schema.py`

Seven RIVA tables plus two from trcore:

```
riva_projects           # Top-level work containers
  ├─ id (PK)
  ├─ name
  ├─ description
  ├─ act_id (nullable FK to Play Acts)
  └─ status: "active" | "archived"

riva_plans              # Ollama-decomposed plans
  ├─ id (PK)
  ├─ project_id (FK)
  ├─ agent_id (nullable)
  ├─ title
  ├─ user_request
  ├─ decomposition_json
  └─ status: "draft" | "decomposing" | "ready"

riva_plan_steps         # Plan steps
  ├─ id (PK)
  ├─ plan_id (FK)
  ├─ step_number
  ├─ title
  ├─ description
  ├─ acceptance_criterion
  └─ status

riva_contracts          # Approved, enforceable plans
  ├─ id (PK)
  ├─ plan_id (FK, UNIQUE)
  ├─ agent_id (FK)
  ├─ verification_criteria_json
  ├─ status: "active" | "cancelled" | "completed"
  └─ approved_at

riva_audits             # Post-completion verifications
  ├─ id (PK)
  ├─ contract_id (FK)
  ├─ agent_id (FK)
  ├─ criteria_results_json
  ├─ overall_verdict: "passed" | "failed" | "inconclusive"
  └─ audited_at

riva_agent_properties   # DB-backed agent config (source of truth)
  ├─ id (PK)
  ├─ agent_id (FK, UNIQUE)
  ├─ claude_md_content
  ├─ permissions_json
  ├─ hooks_config_json
  ├─ env_vars_json
  ├─ synced_at (NULL if dirty)
  └─ created_at

riva_agent_sessions     # Session audit trail
  ├─ id (PK)
  ├─ agent_id (FK)
  ├─ contract_id (FK, nullable)
  ├─ project_id (FK, nullable)
  ├─ status: "running" | "stopped"
  ├─ trigger: "riva_dispatch" | "manual"
  └─ created_at

cc_agents               # From trcore (agent process metadata)
  ├─ id (PK)
  ├─ username
  ├─ name
  ├─ slug (UNIQUE)
  ├─ purpose
  ├─ cwd (workspace directory)
  ├─ session_id (for Claude Code resume support)
  └─ created_at

cc_history              # From trcore (persistent conversation history)
  ├─ id (PK)
  ├─ agent_id (FK)
  ├─ role: "user" | "assistant" | "error"
  ├─ content
  └─ created_at
```

All RIVA tables use IF NOT EXISTS for idempotent migration.

---

## CCManager: Agent Process Management

**File:** `trcore/cc_manager.py` (external, but critical)

```python
class CCManager:
    """Manages Claude Code agent lifecycle."""
    
    def __init__(self, db: CCDatabase, 
                 on_session_complete: Callable = None,
                 context_injector: Callable = None):
        """db: CCDatabase protocol (get_connection, transaction)"""
    
    # Agent CRUD (persistent)
    def list_agents(username: str) -> list[dict]
    def create_agent(username: str, name: str, purpose: str = "") -> dict
    def delete_agent(agent_id: str) -> dict
    
    # Session (ephemeral + persistent history)
    async def send_message(agent_id: str, text: str) -> dict
    def poll_events(agent_id: str, since: int = 0) -> dict
    async def stop_session(agent_id: str) -> dict
    def get_history(agent_id: str, limit: int = 100) -> list[dict]
    
    # Streaming (for SSE)
    async def stream_events(agent_id: str, since: int = 0) -> AsyncGenerator
```

**RIVA Integration:**
- `cc_adapter.py :: RivaCCDatabase` implements CCDatabase protocol
- Allows CCManager to read/write RIVA's shared talkingrock.db
- Sessions are stored in cc_agents (metadata) + cc_history (messages)
- RIVA wraps these via RPC handlers to expose them to Tauri

---

## Module Dependencies

```
service.py
  ├─ rpc_dispatcher.py (JSON-RPC dispatch)
  ├─ rpc_handlers/ (all 7 handler modules)
  │  ├─ agents.py → ccmanager, properties_store
  │  ├─ sessions.py → ccmanager, contract_store, properties_store
  │  ├─ audits.py → ccmanager, audit_engine, contract_store
  │  ├─ plans.py → plan_engine, contract_store
  │  ├─ projects.py → play_integration
  │  └─ contracts.py, system.py (standalone)
  ├─ plan_engine.py (Ollama LLM calls)
  ├─ contract_store.py → models, nol_contract
  ├─ audit_engine.py (git verification)
  ├─ properties_store.py (DB config sync)
  ├─ stream_broker.py (pub/sub for TUI)
  ├─ play_integration.py (Play API reads)
  ├─ play_write.py (Play API writes)
  ├─ cc_adapter.py (trcore protocol)
  ├─ db.py (SQLite wrapper)
  ├─ schema.py (migrations)
  ├─ models.py (dataclasses)
  └─ errors.py (exception hierarchy)

trcore/ (external)
  ├─ cc_manager.py (agent process management)
  ├─ cc_db.py (database protocol)
  ├─ entry_guard.py / quick_judge.py (safety + intent)
  └─ ... (atomic ops, providers, etc.)
```

---

## Testing Strategy

**Test locations:** `/home/kellogg/dev/RIVA/tests/`

```
tests/
├── test_*.py (unit tests by module)
├── conftest.py (pytest fixtures)
└── integration/ (end-to-end tests)
```

**Running tests:**
```bash
cd /home/kellogg/dev/RIVA
.venv/bin/pytest tests/ -x --tb=short -q
```

**Key test patterns:**
- Mocking CCManager for agent tests
- Mocking Ollama for plan engine tests
- Using in-memory SQLite for db tests
- Testing RPC dispatcher without a socket (invoke dispatch() directly)

---

## Common Extensions

### Adding a New RPC Method

1. Create handler in `rpc_handlers/new_domain.py`:
   ```python
   def handle_new_method(*, required_param: str, optional: str = None, **_kw) -> dict:
       # Implementation
       return { "result": value }
   ```

2. Register in `service.py :: _register_phaseX_handlers()`:
   ```python
   register_method("riva/new/method", handle_new_method)
   ```

3. Add tests in `tests/test_new_domain.py`

### Adding a New Verification Criterion Type

1. Update `models.py :: VerificationCriterion`:
   ```python
   # Add new type to type field's docstring
   ```

2. Update `contract_store.py :: _parse_criterion_from_text()`:
   ```python
   if text_lower.startswith("new_type:"):
       # Parse and return VerificationCriterion(type="new_type", ...)
   ```

3. Update `audit_engine.py`:
   ```python
   def verify_new_type(criterion, cwd) -> tuple[bool, str]:
       # Implementation
       return (passed, evidence_text)
   ```

### Extending Agent Properties

1. Add field to `riva_agent_properties` table (schema.py)
2. Update `properties_store.py` getters/setters
3. Update property sync logic to read/write the new field

---

## Debugging Tips

### Enabling Debug Logging

```bash
export RUST_LOG=debug
riva-service  # Runs with debug output
```

### Inspecting the Database

```bash
sqlite3 ~/.talkingrock/talkingrock.db
sqlite> SELECT * FROM riva_plans LIMIT 1;
sqlite> SELECT * FROM riva_contracts;
```

### Testing RPC Directly

```python
from riva.rpc_dispatcher import dispatch

request = '{"jsonrpc": "2.0", "method": "riva/ping", "id": 1}'
response = dispatch(request)
print(response)
```

### Agent Process Inspection

```bash
ps aux | grep claude   # See running claude processes
ls -la ~/dev/talkingrock/agents/  # Agent workspaces
```

---

## Known Technical Debt

- [ ] CCManager is not transaction-aware (race conditions on concurrent sends)
- [ ] Event buffer pruning at 10K entries loses older context
- [ ] Plan decomposition is blocking (blocks RPC dispatch while Ollama responds)
- [ ] No rate limiting on RPC calls
- [ ] Agent properties sync is not atomic (TOCTOU race)
- [ ] Manual verification criteria require external UI acknowledgment

