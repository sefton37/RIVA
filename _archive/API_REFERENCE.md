# RIVA API Reference — Complete Guide for Tauri Frontend

## Overview

RIVA is a recursive intent verification orchestrator that coordinates Claude Code agents through plans, contracts, audits, and projects. The Tauri frontend communicates with RIVA via JSON-RPC 2.0 over a Unix domain socket at `~/.talkingrock/riva.sock`.

**Key characteristics:**
- Length-prefixed JSON-RPC 2.0 protocol (4-byte big-endian uint32 length prefix)
- All methods are synchronous (request/response), except polling for agent stream events
- Database: shared `talkingrock.db` (SQLite with WAL mode)
- Agent process management via CCManager from trcore
- Entry guard screening for safety + intent before plan creation

---

## Communication Protocol

### Message Format

**Request:**
```
[4-byte length prefix: uint32 big-endian]
{
  "jsonrpc": "2.0",
  "method": "riva/...",
  "params": { ... },
  "id": <number or string>
}
```

**Response:**
```
[4-byte length prefix: uint32 big-endian]
{
  "jsonrpc": "2.0",
  "result": { ... },  // OR
  "error": {
    "code": <number>,
    "message": "<string>",
    "data": { ... }
  },
  "id": <same as request>
}
```

### Error Codes

| Code | Meaning |
|------|---------|
| -32700 | Parse error |
| -32600 | Invalid request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |
| -32001 | Entry guard blocked |

---

## Complete RPC Method Reference

### System Methods (Phase 1)

#### `riva/ping`
Health check.

**Params:** none

**Response:**
```json
{
  "result": "pong"
}
```

---

#### `riva/status`
Service status with uptime and version.

**Params:** none

**Response:**
```json
{
  "status": "running",
  "uptime_seconds": 3600.5,
  "version": "0.1.0"
}
```

---

### Plan Methods (Phase 2)

#### `riva/plan/create`
Start async plan generation from a user request. **Guarded by entry guard.**

**Params:**
```json
{
  "project_id": "proj-abc123",
  "user_request": "Build a login form with email validation"
}
```

**Entry Guard:** Checks the `user_request` param for safety (no exfiltration, malicious prompts) and intent alignment. Can be disabled by not passing a provider to dispatch().

**Response:**
```json
{
  "plan_id": "plan-xyz789",
  "status": "draft"
}
```

**Errors:**
- Missing `project_id` or `user_request`
- Entry guard rejection (code -32001, includes `data` with guard_type and reason)

---

#### `riva/plan/status`
Poll plan decomposition status.

**Params:**
```json
{
  "plan_id": "plan-xyz789"
}
```

**Response:**
```json
{
  "id": "plan-xyz789",
  "project_id": "proj-abc123",
  "title": "Add Login Form",
  "user_request": "Build a login form...",
  "status": "ready",  // "draft" | "decomposing" | "ready"
  "estimated_minutes": 45,
  "risks": ["requires backend auth service"],
  "steps": [...],
  "created_at": "2026-03-22T10:30:00Z",
  "updated_at": "2026-03-22T10:30:45Z"
}
```

---

#### `riva/plan/get`
Get full plan with all steps.

**Params:**
```json
{
  "plan_id": "plan-xyz789"
}
```

**Response:** Same structure as `riva/plan/status`.

The `steps` array contains:
```json
{
  "id": "step-1",
  "step_number": 1,
  "title": "Design the form layout",
  "description": "Use Pico CSS...",
  "acceptance_criterion": "file_exists: src/form.html",
  "estimated_minutes": 15,
  "status": "pending"
}
```

---

#### `riva/plan/list`
List all plans for a project.

**Params:**
```json
{
  "project_id": "proj-abc123",
  "status": "ready"  // optional: "draft", "decomposing", "ready"
}
```

**Response:**
```json
{
  "plans": [
    { /* plan object */ },
    ...
  ]
}
```

---

#### `riva/plan/approve`
Approve a plan and create a contract.

**Params:**
```json
{
  "plan_id": "plan-xyz789",
  "agent_id": "agent-abc123"
}
```

**Response:** Contract object (see Contract section below).

---

### Contract Methods (Phase 2)

#### `riva/contract/get`
Get a contract by ID.

**Params:**
```json
{
  "contract_id": "contract-abc123"
}
```

**Response:**
```json
{
  "id": "contract-abc123",
  "plan_id": "plan-xyz789",
  "agent_id": "agent-abc123",
  "verification_criteria": [
    {
      "type": "file_exists",
      "path": "src/form.html"
    },
    {
      "type": "function_defined",
      "file": "src/validators.ts",
      "name": "validateEmail"
    },
    {
      "type": "git_contains_change",
      "path": "src/"
    },
    {
      "type": "git_commit_message",
      "keyword": "feature: login form"
    },
    {
      "type": "manual_verification",
      "description": "Test in production environment"
    }
  ],
  "nol_assembly": "contract { ... }",  // NOL assembly text
  "nol_intent_hash": "sha256-...",
  "nol_verified": true,
  "approved_at": "2026-03-22T10:35:00Z",
  "approved_by": "user",
  "status": "active",  // "active" | "cancelled" | "completed"
  "created_at": "2026-03-22T10:35:00Z",
  "updated_at": "2026-03-22T10:35:00Z"
}
```

**Verification Criterion Types:**
- `file_exists`: path must exist in agent workspace
- `function_defined`: function name must be grep-able in file
- `git_contains_change`: path must appear in git diff
- `git_commit_message`: keyword must appear in git log
- `manual_verification`: always inconclusive, flagged for user review

---

#### `riva/contract/list`
List contracts with optional status filter.

**Params:**
```json
{
  "status": "active"  // optional: "active" | "cancelled" | "completed"
}
```

**Response:**
```json
{
  "contracts": [
    { /* contract object */ },
    ...
  ]
}
```

---

#### `riva/contract/cancel`
Cancel an active contract.

**Params:**
```json
{
  "contract_id": "contract-abc123"
}
```

**Response:**
```json
{
  "contract_id": "contract-abc123",
  "status": "cancelled"
}
```

---

### Agent Methods (Phase 3)

#### `riva/agents/list`
List all agents for the current user.

**Params:** none

**Response:**
```json
{
  "agents": [
    {
      "id": "agent-abc123",
      "name": "Frontend Specialist",
      "slug": "frontend-specialist",
      "purpose": "Build responsive UIs with Pico CSS",
      "busy": false,
      "has_properties": true,
      "synced": true
    },
    ...
  ]
}
```

---

#### `riva/agents/get`
Get agent details including properties.

**Params:**
```json
{
  "agent_id": "agent-abc123"
}
```

**Response:**
```json
{
  "id": "agent-abc123",
  "name": "Frontend Specialist",
  "slug": "frontend-specialist",
  "purpose": "Build responsive UIs with Pico CSS",
  "cwd": "/home/user/dev/talkingrock/agents/frontend-specialist",
  "busy": false,
  "properties": {
    "id": "props-abc123",
    "agent_id": "agent-abc123",
    "claude_md_content": "# Frontend Specialist\n...",
    "permissions_json": { ... },
    "hooks_config_json": { ... },
    "env_vars_json": { ... },
    "synced_at": "2026-03-22T10:30:00Z",
    "created_at": "2026-03-22T10:00:00Z",
    "updated_at": "2026-03-22T10:30:00Z"
  }
}
```

---

#### `riva/agents/create`
Create a new agent with workspace.

**Params:**
```json
{
  "name": "Frontend Specialist",
  "purpose": "Build responsive UIs with Pico CSS"
}
```

**Response:**
```json
{
  "id": "agent-abc123",
  "name": "Frontend Specialist",
  "slug": "frontend-specialist",
  "purpose": "Build responsive UIs with Pico CSS",
  "cwd": "/home/user/dev/talkingrock/agents/frontend-specialist"
}
```

**Side effects:**
- Creates agent workspace directory
- Generates CLAUDE.md, README.md, .gitignore
- Runs `git init` and makes initial commit
- Creates RIVA agent properties in DB
- Creates synthetic conversation for cc memories

---

#### `riva/agents/delete`
Delete an agent.

**Params:**
```json
{
  "agent_id": "agent-abc123"
}
```

**Response:**
```json
{
  "ok": true
}
```

**Side effects:**
- Kills running process if any
- Deletes from cc_agents table
- Preserves workspace directory and properties (audit trail)

---

#### `riva/agents/properties/get`
Get agent properties (CLAUDE.md, permissions, hooks).

**Params:**
```json
{
  "agent_id": "agent-abc123"
}
```

**Response:** Properties object (same structure as `properties` field in `riva/agents/get`).

---

#### `riva/agents/properties/update`
Update CLAUDE.md, permissions, or hooks. Sets `synced_at=NULL`.

**Params:**
```json
{
  "agent_id": "agent-abc123",
  "claude_md": "# Updated CLAUDE.md\n...",  // optional
  "permissions": { ... }  // optional
}
```

**Response:** Updated properties object.

**Side effects:**
- Updates DB, sets `synced_at=NULL`
- Does NOT write to disk (use `riva/agents/properties/sync` next)

---

#### `riva/agents/properties/sync`
Sync properties from DB to disk (CLAUDE.md, .claude/permissions.json).

**Params:**
```json
{
  "agent_id": "agent-abc123"
}
```

**Response:**
```json
{
  "synced_at": "2026-03-22T10:30:00Z",
  "files_written": [
    "/home/user/dev/talkingrock/agents/frontend-specialist/CLAUDE.md",
    "/home/user/dev/talkingrock/agents/frontend-specialist/.claude/permissions.json"
  ]
}
```

---

### Session Methods (Phase 4)

#### `riva/session/deploy`
Sync properties then dispatch agent with contract prompt.

**Params:**
```json
{
  "contract_id": "contract-abc123",
  "agent_id": "agent-abc123"
}
```

**Response:**
```json
{
  "session_id": "sess-abc123xyz",
  "agent_id": "agent-abc123",
  "contract_id": "contract-abc123",
  "status": "deployed"
}
```

**Side effects:**
- Syncs agent properties to disk
- Builds dispatch prompt from contract (plan title, steps, acceptance criteria)
- Spawns `claude` CLI process in agent's cwd
- Records session in riva_agent_sessions table
- Sends initial user message (the dispatch prompt) to the agent

**Dispatch Prompt Structure:**
```
# Task: [Plan Title]

Original request: [User Request]

## Steps

### Step 1: [Step Title]
[Description]
**Goal:** [Acceptance Criterion]

...

## Acceptance Criteria

1. [Criterion 1]
2. [Criterion 2]
...

Complete all steps. Commit your work with descriptive messages.
```

---

#### `riva/session/poll`
Poll agent events. For the Tauri 200ms polling path (non-blocking).

**Params:**
```json
{
  "agent_id": "agent-abc123",
  "since": 0
}
```

**Response:**
```json
{
  "events": [
    {
      "type": "user",
      "text": "# Task: Add Login Form\n..."
    },
    {
      "type": "assistant_delta",
      "text": "I'll help you build a login form..."
    },
    {
      "type": "tool_use",
      "tool": "Read",
      "input": "src/main.ts"
    },
    {
      "type": "tool_result",
      "text": "export function main() { ... }",
      "is_error": false
    },
    {
      "type": "assistant_delta",
      "text": "Now I'll create the form..."
    },
    {
      "type": "tool_use",
      "tool": "Write",
      "input": "src/form.html"
    },
    {
      "type": "tool_result",
      "text": "Created src/form.html",
      "is_error": false
    },
    {
      "type": "done"
    }
  ],
  "next_index": 8,
  "busy": false
}
```

**Event Types:**
- `user`: Initial prompt or follow-up message
- `assistant_delta`: Incremental text from assistant (accumulate these)
- `tool_use`: Tool invocation (Read, Write, Bash, etc.)
- `tool_result`: Tool output or error
- `error`: Error message
- `done`: Session complete (no more events)

**Usage Pattern:**
1. Call with `since=0` first time → get all buffered events
2. Save `next_index` from response
3. Call with `since=<next_index>` in next poll → get only new events
4. Stop when you receive a `done` event or `busy=false` with no new events

---

#### `riva/session/stop`
Stop a running agent session.

**Params:**
```json
{
  "agent_id": "agent-abc123"
}
```

**Response:**
```json
{
  "ok": true
}
```

**Side effects:**
- Sends SIGTERM to claude process
- Waits 5 seconds, then SIGKILL if needed
- Appends `done` event to event buffer
- Updates riva_agent_sessions status to "stopped"

---

#### `riva/session/history`
Get conversation history for an agent.

**Params:**
```json
{
  "agent_id": "agent-abc123",
  "limit": 100
}
```

**Response:**
```json
{
  "history": [
    {
      "role": "user",
      "content": "# Task: Add Login Form\n...",
      "created_at": "2026-03-22T10:35:00Z"
    },
    {
      "role": "assistant",
      "content": "I'll help you build a login form. First, let me examine the current structure...",
      "created_at": "2026-03-22T10:35:05Z"
    },
    {
      "role": "user",
      "content": "Done",
      "created_at": "2026-03-22T10:40:00Z"
    },
    ...
  ]
}
```

**Note:** History persists in cc_history table; events buffer is ephemeral and pruned after 10,000 entries.

---

### Audit Methods (Phase 5)

#### `riva/audit/trigger`
Manually trigger an audit for a contract.

**Params:**
```json
{
  "contract_id": "contract-abc123"
}
```

**Response:**
```json
{
  "id": "audit-xyz789",
  "contract_id": "contract-abc123",
  "agent_id": "agent-abc123",
  "triggered_by": "user",
  "git_diff_summary": "3 files changed, 42 insertions(+), 12 deletions(-)",
  "files_changed_json": ["src/form.html", "src/validators.ts", "src/styles.css"],
  "criteria_results_json": {
    "criterion-1": {
      "type": "file_exists",
      "path": "src/form.html",
      "status": "passed",
      "evidence": "File exists at src/form.html"
    },
    "criterion-2": {
      "type": "function_defined",
      "file": "src/validators.ts",
      "name": "validateEmail",
      "status": "passed",
      "evidence": "grep found 'function validateEmail'"
    },
    "criterion-3": {
      "type": "manual_verification",
      "status": "inconclusive",
      "evidence": "Requires human review"
    }
  },
  "overall_verdict": "passed",  // "passed" | "failed" | "inconclusive"
  "verdict_explanation": "All automated criteria passed. 1 manual criterion requires review.",
  "audited_at": "2026-03-22T10:40:00Z",
  "created_at": "2026-03-22T10:40:00Z"
}
```

**Automation:** Audits run automatically after agent deploy completes (on `done` event from StreamBroker). This is an optional manual trigger path.

---

#### `riva/audit/get`
Get an audit result by ID.

**Params:**
```json
{
  "audit_id": "audit-xyz789"
}
```

**Response:** Same structure as `riva/audit/trigger` response.

---

#### `riva/audit/list`
List audits, optionally filtered by contract.

**Params:**
```json
{
  "contract_id": "contract-abc123"  // optional
}
```

**Response:**
```json
{
  "audits": [
    { /* audit object */ },
    ...
  ]
}
```

---

### Project Methods (Phase 5)

#### `riva/projects/create`
Create a new RIVA project.

**Params:**
```json
{
  "name": "Login Feature",
  "description": "Add OAuth2 login to the dashboard",
  "act_id": "act-xyz123"  // optional: link to Play Act
}
```

**Response:**
```json
{
  "id": "proj-abc123",
  "name": "Login Feature",
  "description": "Add OAuth2 login to the dashboard",
  "act_id": "act-xyz123",
  "status": "active",
  "created_at": "2026-03-22T10:30:00Z"
}
```

---

#### `riva/projects/list`
List projects with optional status filter.

**Params:**
```json
{
  "status": "active"  // optional: "active" | "archived"
}
```

**Response:**
```json
{
  "projects": [
    {
      "id": "proj-abc123",
      "name": "Login Feature",
      "description": "...",
      "act_id": "act-xyz123",
      "act_title": "Dashboard Enhancement",  // if act_id is set
      "status": "active",
      "created_at": "2026-03-22T10:30:00Z",
      "updated_at": "2026-03-22T10:30:00Z"
    },
    ...
  ]
}
```

---

#### `riva/projects/get`
Get project details.

**Params:**
```json
{
  "project_id": "proj-abc123"
}
```

**Response:**
```json
{
  "id": "proj-abc123",
  "name": "Login Feature",
  "description": "...",
  "act_id": "act-xyz123",
  "act_context": {
    "title": "Dashboard Enhancement",
    "description": "...",
    ...  // full Play Act object
  },
  "status": "active",
  "created_at": "2026-03-22T10:30:00Z",
  "updated_at": "2026-03-22T10:30:00Z"
}
```

---

#### `riva/projects/update`
Update project fields.

**Params:**
```json
{
  "project_id": "proj-abc123",
  "name": "Updated Name",  // optional
  "description": "Updated description",  // optional
  "act_id": "act-xyz456"  // optional
}
```

**Response:** Updated project object (same as `riva/projects/get`).

---

#### `riva/projects/archive`
Archive a project.

**Params:**
```json
{
  "project_id": "proj-abc123"
}
```

**Response:**
```json
{
  "project_id": "proj-abc123",
  "status": "archived"
}
```

---

## Data Model Relationships

### Entity Relationship Diagram

```
riva_projects
  ├─ id (PK)
  ├─ name
  ├─ description
  ├─ act_id (nullable, FK to Play Acts table)
  └─ status

riva_plans
  ├─ id (PK)
  ├─ project_id (FK → riva_projects)
  ├─ agent_id (nullable)
  ├─ title
  ├─ user_request
  ├─ decomposition_json
  └─ status: "draft" | "decomposing" | "ready" | "approved"

riva_plan_steps
  ├─ id (PK)
  ├─ plan_id (FK → riva_plans)
  ├─ step_number
  ├─ title
  ├─ description
  ├─ acceptance_criterion
  └─ status

riva_contracts
  ├─ id (PK)
  ├─ plan_id (FK → riva_plans, UNIQUE)
  ├─ agent_id (FK → cc_agents)
  ├─ verification_criteria_json
  ├─ status: "active" | "cancelled" | "completed"
  └─ approved_at

riva_audits
  ├─ id (PK)
  ├─ contract_id (FK → riva_contracts)
  ├─ agent_id (FK → cc_agents)
  ├─ criteria_results_json
  ├─ overall_verdict: "passed" | "failed" | "inconclusive"
  └─ audited_at

riva_agent_properties
  ├─ id (PK)
  ├─ agent_id (FK → cc_agents, UNIQUE)
  ├─ claude_md_content
  ├─ permissions_json
  ├─ hooks_config_json
  └─ synced_at

riva_agent_sessions
  ├─ id (PK)
  ├─ agent_id (FK → cc_agents)
  ├─ contract_id (FK → riva_contracts, nullable)
  ├─ project_id (FK → riva_projects, nullable)
  ├─ status: "running" | "stopped"
  └─ trigger: "riva_dispatch" | "manual"

cc_agents (from trcore)
  ├─ id (PK)
  ├─ username
  ├─ name
  ├─ slug (UNIQUE)
  ├─ purpose
  ├─ cwd
  ├─ session_id (for Claude Code resume support)
  └─ created_at

cc_history (from trcore)
  ├─ id (PK)
  ├─ agent_id (FK → cc_agents)
  ├─ role: "user" | "assistant" | "error"
  ├─ content
  └─ created_at
```

---

## CCManager Interface

The CCManager from trcore is the underlying agent process manager. RIVA wraps it via RPC handlers but here's the direct interface for reference:

```python
class CCManager:
    # Agent CRUD (database)
    def list_agents(username: str) -> list[dict]
    def create_agent(username: str, name: str, purpose: str = "") -> dict
    def delete_agent(agent_id: str) -> dict

    # Session (process management)
    async def send_message(agent_id: str, text: str) -> dict
    def poll_events(agent_id: str, since: int = 0) -> dict
    async def stop_session(agent_id: str) -> dict
    def get_history(agent_id: str, limit: int = 100) -> list[dict]

    # Streaming (for SSE)
    async def stream_events(agent_id: str, since: int = 0) -> AsyncGenerator
```

**Key behaviors:**
- `send_message()` spawns `claude --print --output-format stream-json ...` as a subprocess
- `poll_events()` returns new events in a buffer (non-blocking)
- Events accumulate up to 10,000 entries, then are pruned
- History is persisted to cc_history table
- Session resume support via claude's `--resume <session_id>` flag

---

## Typical Workflows

### Creating and Executing a Contract

```
1. riva/projects/create
   → Creates project container

2. riva/plan/create (user_request, project_id)
   → Entry guard checks request
   → PlanEngine (Ollama) decomposes into steps
   → Returns plan_id immediately

3. Poll riva/plan/status (plan_id) until status="ready"

4. riva/plan/get (plan_id)
   → User reviews plan and steps

5. riva/plan/approve (plan_id, agent_id)
   → Generates NOL contract
   → Creates riva_contracts row
   → Returns contract object

6. riva/session/deploy (contract_id, agent_id)
   → Syncs properties to disk
   → Builds dispatch prompt
   → Spawns claude process
   → Sends initial prompt
   → Returns session_id

7. Poll riva/session/poll (agent_id, since=<last_index>)
   → Get streaming events
   → Accumulate assistant_delta events into UI
   → Stop when type="done"

8. riva/audit/trigger (contract_id)
   → Git diff analysis
   → Verification criteria checks
   → Returns audit result

9. (Optional) Emit riva/scene/confirm to Play if audit passed
```

### Managing Agent Properties

```
1. riva/agents/create (name, purpose)
   → Creates workspace, CLAUDE.md, git repo

2. riva/agents/properties/get (agent_id)
   → Read current DB state

3. riva/agents/properties/update (agent_id, claude_md="...", permissions={...})
   → Update DB only, set synced_at=NULL

4. riva/agents/properties/sync (agent_id)
   → Write DB to disk
   → Returns synced_at timestamp

5. Use agent in deploy/poll/stop workflows
```

---

## Important Implementation Details

### Entry Guard

The entry guard screens the `user_request` in `riva/plan/create` before plan generation:

- **Safety check:** Detects exfiltration, prompt injection, malicious intents
- **Intent check:** Verifies the request aligns with project goals
- **Error handling:** Returns -32001 error with guard_type and reason in `data` field

Example error response:
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32001,
    "message": "Entry guard (safety) blocked: Request contains unauthorized system access pattern",
    "data": {
      "guard_type": "safety",
      "reason": "Request contains unauthorized system access pattern"
    }
  },
  "id": 42
}
```

### NOL Assembly

Contracts include NOL (Nolan Object Language) assembly with inline POST conditions:

- Each verification criterion becomes a POST condition comment
- Intent hash (SHA-256) makes contracts content-addressable
- Optional structural verification via nolang binary (if NOLANG_BINARY env var is set)

### Agent Workspace

When an agent is created, RIVA:

1. Creates directory at `/home/user/dev/talkingrock/agents/{slug}/`
2. Generates CLAUDE.md with purpose and workspace guidelines
3. Creates README.md and .gitignore
4. Runs `git init`, `git add -A`, `git commit -m "chore: workspace created by Cairn"`

The agent's `cwd` is stored in cc_agents table and used for all deployed sessions.

### Session Resumption

Claude Code supports session resumption via `--resume <session_id>`. RIVA:

1. Extracts `session_id` from claude's `result` message
2. Updates cc_agents.session_id
3. Next send_message() call uses `--resume` to continue the conversation

### Verification Criteria Types

| Type | Purpose | Fields | Example |
|------|---------|--------|---------|
| `file_exists` | File was created/modified | `path` | `src/form.html` |
| `function_defined` | Function exists in file | `file`, `name` | `validateEmail` in `src/validators.ts` |
| `git_contains_change` | Path appears in git diff | `path` | `src/` has changes |
| `git_commit_message` | Keyword in recent commits | `keyword` | "feature: login" |
| `manual_verification` | Human review required | `description` | "Test in production" |

The audit engine verifies the first 4 types automatically. The 5th requires manual approval.

---

## Error Handling Patterns

### Synchronous Error
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Invalid params: contract_id is required"
  },
  "id": 42
}
```

### Async Polling Error
```json
{
  "events": [
    {
      "type": "error",
      "text": "Failed to start claude: command not found"
    },
    {
      "type": "done"
    }
  ]
}
```

### Entry Guard Rejection
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32001,
    "message": "Entry guard (intent) blocked: Request does not align with project scope",
    "data": {
      "guard_type": "intent",
      "reason": "Request does not align with project scope"
    }
  },
  "id": 42
}
```

---

## Tauri Implementation Tips

### Unix Socket Communication

```typescript
// Pseudocode for Tauri invoking command to communicate with RIVA socket

async function callRiva(method: string, params: object): Promise<object> {
  const request = {
    jsonrpc: "2.0",
    method,
    params,
    id: Math.random()
  };
  
  const json = JSON.stringify(request);
  const buffer = new TextEncoder().encode(json);
  const length = new Uint8Array(4);
  new DataView(length.buffer).setUint32(0, buffer.length, false);
  
  // Send [length][json] to ~/.talkingrock/riva.sock
  const response = await invoke("send_rpc", {
    socketPath: "~/.talkingrock/riva.sock",
    message: Buffer.concat([length, buffer])
  });
  
  return JSON.parse(response);
}
```

### Polling Strategy

For long-running agent sessions, use exponential backoff:

```typescript
let since = 0;
let backoffMs = 200;

while (true) {
  const result = await callRiva("riva/session/poll", {
    agent_id,
    since
  });
  
  for (const event of result.events) {
    handleEvent(event);
    if (event.type === "done") {
      return;
    }
  }
  
  since = result.next_index;
  
  if (!result.busy && result.events.length === 0) {
    // No more events coming
    break;
  }
  
  await sleep(backoffMs);
  backoffMs = Math.min(backoffMs * 1.5, 2000); // Cap at 2s
}
```

### State Management

Recommended Tauri state structure:

```typescript
interface RivaState {
  projects: Map<string, RivaProject>;
  plans: Map<string, RivaPlan>;
  contracts: Map<string, RivaContract>;
  agents: Map<string, Agent>;
  activeSessions: Map<string, SessionState>;
  audits: Map<string, Audit>;
}

interface SessionState {
  sessionId: string;
  agentId: string;
  contractId: string;
  events: Event[];
  nextIndex: number;
  status: "running" | "stopped";
}
```

---

## Known Limitations & Future Work

- **Session persistence:** On RIVA restart, in-memory event buffers are lost (but cc_history persists)
- **Manual verification:** Audit results with manual_verification type require explicit user action
- **NOL verification:** Structural verification requires nolang binary (optional)
- **Parallel agents:** No locking on contract/plan exclusive assignment (first approval wins)

---

## Configuration

RIVA uses environment variables from trcore.settings:

| Env Var | Default | Purpose |
|---------|---------|---------|
| `NOLANG_BINARY` | (not set) | Path to nolang binary for contract verification |
| `OLLAMA_ENDPOINT` | `http://localhost:11434` | Ollama server for plan decomposition |
| `USER` | System $USER | Username for agent list filtering |

---

## Database Location

- **Database:** `~/.talkingrock/talkingrock.db` (shared with Cairn)
- **Socket:** `~/.talkingrock/riva.sock`
- **Agent workspaces:** `~/dev/talkingrock/agents/{slug}/`

All paths use `trcore.settings.data_dir` and `WORKSPACE_ROOT` for customization.

