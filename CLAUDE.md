# RIVA — Recursive Intent Verification Architecture

> Agent orchestrator for project management. Decomposes intent into contracts,
> dispatches Claude Code agents, supervises them live, and audits their output.

## Architecture

RIVA runs as a separate Python process from Cairn, communicating via Unix
domain socket at `~/.talkingrock/riva.sock`. It reuses CCManager from trcore
for agent process management and shares `talkingrock.db` with Cairn.

```
User -> Entry Guard -> Plan Engine -> Contract Store -> Deploy -> Stream -> Audit
                         (Ollama)       (NOL assembly)   (CCManager)        (git)
```

## Key Files

```
src/riva/
├── service.py              # Unix socket server, handler registration
├── rpc_dispatcher.py       # JSON-RPC 2.0 dispatch with entry guard
├── entry_guard.py          # Safety + intent judges via quick_judge
├── plan_engine.py          # Ollama decomposition into structured plans
├── contract_store.py       # Plan -> contract with NOL assembly
├── nol_contract.py         # NOL assembly generation + verification
├── audit_engine.py         # Post-completion verification (5 criterion types)
├── properties_store.py     # DB-backed agent properties (CLAUDE.md, perms)
├── stream_broker.py        # Pub/sub over CCManager events
├── play_integration.py     # Read Act data for plan context
├── play_write.py           # Scene update proposals after passing audits
├── cc_adapter.py           # CCDatabase protocol adapter for trcore
├── models.py               # Dataclasses (RivaPlan, RivaContract, etc.)
├── schema.py               # DB schema (7 riva_* tables)
├── db.py                   # Database access (talkingrock.db)
├── errors.py               # Error hierarchy
├── rpc_handlers/           # RPC endpoint handlers by domain
│   ├── system.py           # ping, status
│   ├── plans.py            # plan CRUD + approve
│   ├── contracts.py        # contract CRUD
│   ├── agents.py           # agent CRUD + properties
│   ├── sessions.py         # deploy, poll, stop, history
│   ├── audits.py           # audit trigger, get, list
│   └── projects.py         # project CRUD + archive
├── devops/                 # DevOps integration clients
│   ├── forgejo.py          # Forgejo REST API client
│   └── woodpecker.py       # Woodpecker CI REST API client
```

## Running

```bash
# Service
riva-service                    # Start the backend
# or
systemctl --user start riva     # Via systemd (install riva.service first)

# UI lives in Cairn Tauri app (agent bar → RIVA)
```

## Testing

```bash
.venv/bin/pytest tests/ -x --tb=short -q
```

## RPC Methods

| Method | Phase | Description |
|--------|-------|-------------|
| `riva/ping` | 1 | Health check |
| `riva/status` | 1 | Service uptime and version |
| `riva/plan/create` | 2 | Start async plan generation (guarded) |
| `riva/plan/status` | 2 | Poll plan decomposition status |
| `riva/plan/get` | 2 | Get full plan with steps |
| `riva/plan/list` | 2 | List plans for a project |
| `riva/plan/approve` | 2 | Approve plan, create NOL contract |
| `riva/contract/get` | 2 | Get contract details |
| `riva/contract/list` | 2 | List contracts |
| `riva/contract/cancel` | 2 | Cancel active contract |
| `riva/agents/list` | 3 | List agents with properties status |
| `riva/agents/get` | 3 | Get agent details + properties |
| `riva/agents/create` | 3 | Create agent + workspace + properties |
| `riva/agents/delete` | 3 | Delete agent |
| `riva/agents/properties/get` | 3 | Get CLAUDE.md, permissions, hooks |
| `riva/agents/properties/update` | 3 | Update properties (sets synced_at=NULL) |
| `riva/agents/properties/sync` | 3 | Write DB properties to disk |
| `riva/session/deploy` | 4 | Sync + dispatch agent with contract prompt |
| `riva/session/poll` | 4 | Poll agent stream events |
| `riva/session/stop` | 4 | Stop running agent |
| `riva/session/history` | 4 | Get conversation history |
| `riva/audit/trigger` | 5 | Manually trigger contract audit |
| `riva/audit/get` | 5 | Get audit result |
| `riva/audit/list` | 5 | List audits |
| `riva/projects/create` | 5 | Create project (optional Act link) |
| `riva/projects/list` | 5 | List projects |
| `riva/projects/get` | 5 | Get project details |
| `riva/projects/update` | 5 | Update project |
| `riva/projects/archive` | 5 | Archive project |
| `riva/scene/confirm` | 6 | Mark Scene complete (user-confirmed) |

## NOL Integration

Contracts include NOL assembly with inline POST conditions:
- Each acceptance criterion becomes a `; POST[n]:` comment
- Intent hash (SHA-256) makes contracts content-addressable
- Optional structural verification via nolang binary
- Set `NOLANG_BINARY` env var to enable verification

## Conventions

- Python 3.12+, ruff for linting (100 char line length)
- All tables prefixed `riva_*` in shared talkingrock.db
- DB is source of truth for agent properties; disk is derived
- Entry guard screens all plan creation requests (fail-open)
- Git non-zero return codes = inconclusive, never failed
- RIVA never writes Play state without user confirmation
