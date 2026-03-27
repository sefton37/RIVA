# RIVA — Project & Product Management Service

> Project and product management backend. Manages epics, issues, cycles,
> roadmap, research, projects, and integrates with Forgejo/Woodpecker CI.

## Architecture

RIVA runs as a Python process communicating via Unix domain socket at
`~/.talkingrock/riva.sock`. It shares `talkingrock.db` with Cairn.

```
Client -> JSON-RPC 2.0 -> Dispatcher -> PM / Projects / DevOps handlers
```

## Key Files

```
src/riva/
├── service.py              # Unix socket server, handler registration
├── rpc_dispatcher.py       # JSON-RPC 2.0 dispatch
├── models.py               # PM dataclasses (Epic, Issue, Cycle, etc.)
├── pm_store.py             # PM CRUD operations
├── schema.py               # DB schema (riva_projects + pm_* tables)
├── db.py                   # Database access (talkingrock.db)
├── errors.py               # Error hierarchy
├── rpc_handlers/           # RPC endpoint handlers by domain
│   ├── system.py           # ping, status
│   ├── projects.py         # project CRUD + archive
│   ├── pm.py               # epics, issues, cycles, roadmap, research
│   └── devops.py           # Forgejo + Woodpecker CI integration
├── devops/                 # DevOps API clients
│   ├── forgejo.py          # Forgejo REST API client
│   └── woodpecker.py       # Woodpecker CI REST API client
```

## Running

```bash
riva-service                    # Start the backend
```

## Testing

```bash
.venv/bin/pytest tests/ -x --tb=short -q
```

## RPC Methods

| Method | Description |
|--------|-------------|
| `riva/ping` | Health check |
| `riva/status` | Service uptime and version |
| `riva/projects/create` | Create project |
| `riva/projects/list` | List projects |
| `riva/projects/get` | Get project details |
| `riva/projects/update` | Update project |
| `riva/projects/archive` | Archive project |
| `riva/pm/epics/*` | Epic CRUD + archive (5 methods) |
| `riva/pm/issues/*` | Issue CRUD (4 methods) |
| `riva/pm/cycles/*` | Cycle CRUD + issue linking (7 methods) |
| `riva/pm/roadmap/*` | Roadmap CRUD + epic linking (6 methods) |
| `riva/pm/research/*` | Research CRUD (4 methods) |
| `riva/pm/dashboard` | Aggregated PM stats |
| `riva/devops/status` | DevOps connectivity status |
| `riva/devops/repos/*` | Forgejo repo operations (4 methods) |
| `riva/devops/pr/*` | Forgejo PR operations (3 methods) |
| `riva/devops/ci/*` | Woodpecker CI operations (5 methods) |

## Conventions

- Python 3.12+, ruff for linting (100 char line length)
- PM tables prefixed `pm_*`, project table `riva_projects` in shared talkingrock.db
- SQLite + WAL mode for all persistence
- Verbose errors, never silent failures

## Archive

Agent interaction code (contracts, plans, audits, CCManager, entry guard,
stream broker, etc.) was stripped out and archived in `_archive/` on 2026-03-26.
This code may be referenced but is not part of the active codebase.
