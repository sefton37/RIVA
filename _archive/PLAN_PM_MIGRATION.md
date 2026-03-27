# Plan: Migrate product.db PM Tables into talkingrock.db as pm_* Tables

## Context

The product management database lives at `~/talking-rock/product/db/product.db` as a standalone
SQLite file maintained by hand via the `query.sh` script. RIVA already owns `talkingrock.db` (the
shared database with Cairn) and manages 7 `riva_*` tables through `src/riva/schema.py`.

The goal is to move all PM tables into RIVA's ownership so that RIVA RPC handlers can create
epics, issues, cycles, and research entries programmatically — linking them to RIVA-native
concepts (epics to Play Acts, issues to RIVA contracts). The standalone `product.db` becomes
read-only historical record after migration.

**Current data to migrate (verified from product.db):**
- 5 epics (integer IDs 1–5)
- 24 issues (integer IDs 1–24; epic_id FKs to epics 1 or 5; cycle_id column present but NULL on
  all rows)
- 2 cycles (integer IDs 1–2)
- 0 cycle_issues rows
- 5 roadmap items (integer IDs 1–5)
- 0 roadmap_epics rows
- 24 research entries (integer IDs 1–24; epic_id and issue_id NULL on all rows)

---

## Approach (Recommended)

**Single-phase schema expansion + migration script + store layer.**

Add all PM tables to `schema.py` under the `_RIVA_TABLES_SQL` block (or a companion SQL constant),
keeping `ensure_schema()` as the single entry point. A one-time script reads `product.db`, remaps
integer IDs to UUID-style text IDs, and inserts into `talkingrock.db`. A new `pm_store.py` provides
CRUD functions following the exact patterns of `contract_store.py` and `properties_store.py`.

This approach is chosen because:
- It extends an existing, tested schema pattern rather than introducing a second schema file or
  a second call path in startup.
- ID remapping at migration time is safer than carrying integer IDs forward, because all other
  RIVA IDs are `text-{hex12}` style; mixing integer and text PKs in the same DB invites join bugs.
- `ensure_schema()` accepting an optional `conn` is already the right interface — no service
  startup changes are needed.

## Alternatives Considered

**A. Keep product.db, add a read adapter in RIVA.**
Write a `pm_adapter.py` that opens `product.db` as a second connection and returns data from it.

- Pro: zero migration risk, no data moved.
- Con: two DB files, two connection pools, cross-file FK enforcement is impossible, RIVA cannot
  write new PM data without managing both files. Ruled out because RIVA issue #17 (PM RPC
  handlers) requires full write access.

**B. Separate schema file `pm_schema.py`, separate `ensure_pm_schema()` call.**
Mirror the structure but keep PM tables in a sibling module to avoid `schema.py` growing large.

- Pro: cleaner module separation.
- Con: adds a second startup call site, increases the chance that service startup misses one call.
  The current `schema.py` pattern is one module → one `ensure_schema()` → all tables. Keeping that
  invariant is lower risk. A second constant (`_PM_TABLES_SQL`) inside the same file achieves the
  same readability benefit without the fragmentation cost.

---

## Implementation Steps (Ordered by Dependency)

### Step 1 — Extend `src/riva/schema.py`

Add a `_PM_TABLES_SQL` string constant below `_RIVA_TABLES_SQL`. Extend `ensure_schema()` to also
call `conn.executescript(_PM_TABLES_SQL)`.

**Exact SQL for the new tables:**

```sql
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
```

**FK notes:**
- `pm_issues.epic_id` → `pm_epics(id)` and `pm_issues.cycle_id` → `pm_cycles(id)`: nullable FKs.
  SQLite does not enforce NOT NULL on FK columns; the FOREIGN KEY clause only fires when the value
  is non-NULL.
- `pm_issues.riva_contract_id` → `riva_contracts(id)`: nullable cross-prefix FK. This works in
  SQLite with `PRAGMA foreign_keys=ON` because `riva_contracts` is in the same DB file. RIVA's
  `db.py` already enables this pragma.
- `pm_epics.act_id`: intentionally has no FK constraint. Acts live in the Play schema (Cairn's
  tables), which may or may not be in the same DB file depending on deployment. A nullable TEXT
  column with no enforced FK matches the pattern already used in `riva_projects.act_id`.
- The `product.db` triggers (`epics_updated`, etc.) are NOT reproduced. RIVA store functions
  always write `updated_at` explicitly, matching the pattern in `contract_store.py` and
  `properties_store.py`. This is the correct pattern — triggers are a crutch for hand-edited DBs.

### Step 2 — Write the migration script `scripts/migrate_product_db.py`

Create `scripts/` directory at the RIVA project root. The script is a self-contained Python
module (not a RIVA package import) that:

1. Accepts optional `--source` (default: `~/.../product.db`) and `--dest` (default:
   `~/.talkingrock/talkingrock.db`) paths as CLI arguments.
2. Opens both DBs in the same process. **Does not use `riva.db.get_connection`** — this script
   runs before the migration is applied and must not depend on the in-process schema state.
3. Calls `ensure_schema()` on the destination DB first, so pm_* tables exist.
4. Builds ID remapping dicts for each table before inserting:
   - Pattern: `f"{table_prefix}-{str(old_int_id).zfill(11)}"` — e.g., old epic id=5 →
     `"epic-00000000005"`. This is intentionally deterministic and human-readable; a re-run
     produces the same IDs and can be made idempotent with `INSERT OR IGNORE`.
   - Prefixes: `epic-`, `cycle-`, `issue-`, `road-`, `res-`
5. Insertion order respects FK dependencies:
   - `pm_epics` (no deps)
   - `pm_cycles` (no deps)
   - `pm_roadmap` (no deps)
   - `pm_issues` (depends on pm_epics, pm_cycles)
   - `pm_cycle_issues` (depends on pm_cycles, pm_issues)
   - `pm_roadmap_epics` (depends on pm_roadmap, pm_epics)
   - `pm_research` (depends on pm_epics, pm_issues)
6. Remaps nullable FK columns: if `epic_id` is NULL in source, write NULL in dest; if set,
   look up in the remapping dict.
7. Preserves all other columns as-is (TEXT, NULL-safe).
8. After all inserts, runs a row-count verification: compares source and dest counts for each
   table and prints a pass/fail summary.
9. Wraps all destination writes in a single transaction; rolls back if any insert fails.
10. Prints a structured summary on completion: rows migrated per table, any skipped rows,
    and the ID mapping for epics (since those are most likely referenced elsewhere).

**Migration does not delete product.db.** That file should be kept as historical record until
the user explicitly retires it.

### Step 3 — Write `src/riva/pm_store.py`

New module. Provides CRUD for all PM tables. Structure mirrors `contract_store.py`:

- Uses `from riva.db import get_connection, transaction`
- All read functions open `readonly=True` connections, close in `finally`
- All write functions use `with transaction() as conn:`
- Returns `dict[str, Any]` or `dataclass` instances (see Step 4 for models)
- Raises `PmError` (new error subclass, see Step 5) for not-found and validation failures

**Functions to implement (grouped by table):**

```
# Epics
create_epic(name, *, status, project, priority, target_quarter, owner,
            description, success_criteria, notes, act_id) -> dict
get_epic(epic_id) -> dict | None
list_epics(*, status=None, project=None) -> list[dict]
update_epic(epic_id, **fields) -> dict
archive_epic(epic_id) -> None        # sets status='Archived'

# Cycles
create_cycle(name, *, status, start_date, end_date, goal) -> dict
get_cycle(cycle_id) -> dict | None
list_cycles(*, status=None) -> list[dict]
update_cycle(cycle_id, **fields) -> dict
add_issue_to_cycle(cycle_id, issue_id) -> None
remove_issue_from_cycle(cycle_id, issue_id) -> None
get_cycle_issues(cycle_id) -> list[dict]   # full issue rows

# Issues
create_issue(name, *, status, priority, type, epic_id, cycle_id, estimate,
             assignee, forgejo_link, branch, acceptance_criteria, notes,
             riva_contract_id) -> dict
get_issue(issue_id) -> dict | None
list_issues(*, status=None, epic_id=None, cycle_id=None) -> list[dict]
update_issue(issue_id, **fields) -> dict

# Roadmap
create_roadmap_item(name, *, status, quarter, project, description, why,
                    dependencies) -> dict
get_roadmap_item(roadmap_id) -> dict | None
list_roadmap(*, quarter=None, project=None) -> list[dict]
update_roadmap_item(roadmap_id, **fields) -> dict
link_epic_to_roadmap(roadmap_id, epic_id) -> None
unlink_epic_from_roadmap(roadmap_id, epic_id) -> None

# Research
create_research(name, *, type, status, project, epic_id, issue_id, source,
                key_finding, date, tags, doc_path) -> dict
get_research(research_id) -> dict | None
list_research(*, project=None, type=None, epic_id=None) -> list[dict]
update_research(research_id, **fields) -> dict
```

ID generation pattern (from `contract_store.py` line 145):
```python
epic_id = f"epic-{uuid4().hex[:12]}"
```

Use these prefixes consistently:
- `epic-`, `cycle-`, `issue-`, `road-`, `res-`

The `update_*` functions should use the dynamic SQL pattern from `projects.py` (lines 133–155):
build a list of `"col=?"` fragments and `params`, append `updated_at=?`, then execute.

### Step 4 — Add PM dataclasses to `src/riva/models.py`

Append to the existing file. Five new dataclasses:

- `PmEpic` — fields matching `pm_epics` columns + `to_dict()`
- `PmCycle` — fields matching `pm_cycles` columns + `to_dict()`
- `PmIssue` — fields matching `pm_issues` columns + `to_dict()`
- `PmRoadmapItem` — fields matching `pm_roadmap` columns + `to_dict()`
- `PmResearch` — fields matching `pm_research` columns + `to_dict()`

All use `@dataclass` (not frozen). All `to_dict()` methods return `dict[str, Any]` suitable for
JSON serialization (no None-stripping needed — callers can decide).

**Decision on dataclasses vs. returning `dict`:** The existing `contract_store.py` returns
`RivaContract` dataclasses; `properties_store.py` returns raw `dict`. For PM tables the
store should return dataclasses for epics, cycles, issues, roadmap (these will be exposed in
RPC responses and need `to_dict()`), but returning `dict` is acceptable for the join-table
operations (`add_issue_to_cycle`, etc.) which have no meaningful return value beyond `None`.

### Step 5 — Add `PmError` to `src/riva/errors.py`

Add one line:

```python
class PmError(RivaError):
    """Error during PM table operations (not found, validation)."""
```

This follows the existing error-per-domain pattern (line 35: `ContractError`, line 39:
`AuditError`, etc.).

### Step 6 — Write `tests/test_pm_schema.py`

New test file. Tests that:

1. `ensure_schema()` creates all 7 `pm_*` tables (extend the existing table-name assertion
   pattern from `test_schema.py` line 29).
2. `pm_issues.epic_id` FK is enforced: inserting an issue with a non-existent `epic_id` raises
   `sqlite3.IntegrityError`.
3. `pm_issues.riva_contract_id` FK is enforced: inserting an issue with a non-existent
   `riva_contract_id` raises `sqlite3.IntegrityError`.
4. `pm_cycle_issues` composite PK prevents duplicates.
5. `pm_roadmap_epics` composite PK prevents duplicates.
6. All tables support NULL on nullable columns (smoke test).

### Step 7 — Write `tests/test_pm_store.py`

New test file. Follows the fixture pattern from `test_properties_store.py` (lines 23–59):
a `db_setup` fixture that creates a tmp DB, runs `ensure_schema()`, then patches
`riva.pm_store.get_connection` and `riva.pm_store.transaction` to point at the tmp DB.

Test classes:

```
TestEpicStore         — create, get, list (with/without filter), update, archive
TestCycleStore        — create, get, list, update, add/remove issue, get_cycle_issues
TestIssueStore        — create, get, list (filtered by status/epic/cycle), update,
                        create with riva_contract_id link
TestRoadmapStore      — create, get, list, update, link/unlink epic
TestResearchStore     — create, get, list (filtered), update
TestPmErrors          — get_epic(nonexistent) → None, update_epic(nonexistent) → PmError,
                        archive_epic(nonexistent) → PmError
```

Each `create_*` test verifies: ID has correct prefix, timestamps are set, all passed fields
are persisted. Each `update_*` test verifies: `updated_at` changes, unchanged fields are
preserved.

### Step 8 — Update `tests/test_schema.py`

The existing `test_creates_all_tables` at line 15 asserts `tables == expected` where `expected`
is a hardcoded set of 7 `riva_*` table names. This will fail after Step 1 adds 7 `pm_*` tables.

Two options:
- Change the query to `WHERE name LIKE 'riva_%'` only (already the case — line 24 already
  filters by `riva_%`, so `pm_*` tables will not appear in that query). **No change needed** to
  the `riva_%` assertion.
- Add a second assertion block checking for the 7 `pm_*` table names.

The correct action is to **add a second assertion block** in `test_creates_all_tables` that
checks `pm_*` tables exist. The existing `riva_%` assertion stays untouched.

---

## Files Affected

| File | Action | Notes |
|------|--------|-------|
| `src/riva/schema.py` | Modify | Add `_PM_TABLES_SQL` constant; extend `ensure_schema()` |
| `src/riva/models.py` | Modify | Append 5 new PM dataclasses |
| `src/riva/errors.py` | Modify | Add `PmError` |
| `src/riva/pm_store.py` | Create | ~350 lines; full CRUD for all PM tables |
| `scripts/migrate_product_db.py` | Create | ~150 lines; one-time migration script |
| `tests/test_pm_schema.py` | Create | Schema FK and constraint tests |
| `tests/test_pm_store.py` | Create | Full CRUD coverage for pm_store |
| `tests/test_schema.py` | Modify | Add `pm_*` table assertion block |

**No changes needed to:**
- `db.py` — connection and transaction primitives are already correct
- `service.py` — RPC handlers are registered separately (issue #17 covers that)
- `rpc_dispatcher.py` — no new methods in this plan

---

## Risks & Mitigations

**R1 — FK ordering in `ensure_schema()` when `foreign_keys=ON`.**
`executescript()` commits any pending transaction and disables foreign key checks for its
duration in some SQLite builds. Empirically, `IF NOT EXISTS` CREATE TABLE statements execute
in source order; as long as parent tables appear before child tables in `_PM_TABLES_SQL`,
there is no risk. The order given in Step 1 is: `pm_epics`, `pm_cycles`, `pm_roadmap`,
`pm_issues` (refs epics + cycles), `pm_cycle_issues` (refs cycles + issues),
`pm_roadmap_epics` (refs roadmap + epics), `pm_research` (refs epics + issues).
Mitigation: test FK enforcement in `test_pm_schema.py` (Step 6).

**R2 — `pm_issues.riva_contract_id` cross-prefix FK may be enforced before `riva_contracts`
exists.**
If someone calls `ensure_schema()` on a fresh DB, `_RIVA_TABLES_SQL` runs first (inside the
existing `executescript()` call), then `_PM_TABLES_SQL` runs second. `riva_contracts` will
already exist by the time `pm_issues` is created. No risk, as long as the order in
`ensure_schema()` is preserved.
Mitigation: test order is documented in code comments.

**R3 — Migration script run multiple times.**
Using `INSERT OR IGNORE` and deterministic ID generation (zero-padded integer) makes re-runs
safe. The script should also print a warning if destination rows already exist for a given
source ID.
Mitigation: deterministic IDs + `INSERT OR IGNORE` + count-diff verification at the end.

**R4 — `product.db` integer IDs are referenced in external tools (query.sh, docs).**
The migration remaps IDs. Any manual reference to e.g. `epic id=5` in shell history or
markdown notes will not match the new `epic-00000000005` form.
Mitigation: the migration script prints a full ID mapping table. The user should archive
`product.db` and update `query.sh` to target `talkingrock.db`. This is out of scope for this
plan but should be tracked as a follow-on task.

**R5 — `test_schema.py` `test_creates_all_tables` breaks if implementer forgets Step 8.**
The test currently filters `WHERE name LIKE 'riva_%'` so it will NOT break — `pm_*` tables are
invisible to the existing query. The risk is the opposite: the test passes even if pm_* tables
were never created.
Mitigation: Step 8 adds an explicit `pm_*` assertion block so schema completeness is verified.

**R6 — `pm_store.py` update functions use dynamic SQL string formatting.**
The `handle_projects_update` pattern (projects.py lines 133–155) builds SQL from a list of
`"col=?"` strings. If an implementer inadvertently passes unsanitized column names, this is an
injection vector.
Mitigation: `update_*` functions must use an explicit allowlist of valid column names and raise
`PmError("Unknown field: X")` for anything not in the list. Document this in the module docstring.

---

## Testing Strategy

**Unit tests (Steps 6 and 7):** Use tmp_path + mock injection (same pattern as
`test_properties_store.py`). No real `talkingrock.db` is touched. All store functions are tested
against an in-process SQLite DB.

**Schema tests (Step 6):** Verify structural guarantees — FK enforcement, composite PKs,
NULL-ability — not just table existence.

**Migration script (Step 2):** Manual verification only. Run against a copy of `product.db`,
inspect the output, confirm row counts and a sample of remapped rows. The script's built-in
count verification serves as a smoke test. Automated testing of the migration script is
out-of-scope (it is a one-time tool with no ongoing test maintenance burden).

**Coverage target:** `pm_store.py` should reach >90% line coverage via `test_pm_store.py`.
The `update_*` allowlist validation paths must each be covered.

---

## Definition of Done

- [ ] `src/riva/schema.py` contains `_PM_TABLES_SQL` with all 7 `pm_*` CREATE TABLE statements
- [ ] `ensure_schema()` executes both SQL blocks; confirmed by `test_pm_schema.py`
- [ ] All FK constraints in `pm_*` tables are verified by test assertions
- [ ] `pm_issues.act_id` column removed (acts link is on `pm_epics`, not issues) — confirm
      no act_id column appears in pm_issues DDL
- [ ] `src/riva/models.py` contains `PmEpic`, `PmCycle`, `PmIssue`, `PmRoadmapItem`,
      `PmResearch` dataclasses
- [ ] `src/riva/errors.py` contains `PmError`
- [ ] `src/riva/pm_store.py` implements all functions listed in Step 3
- [ ] `pm_store.py` update functions have column allowlist validation
- [ ] `tests/test_pm_schema.py` passes (FK, PK, NULL tests)
- [ ] `tests/test_pm_store.py` passes (full CRUD coverage)
- [ ] `tests/test_schema.py` asserts both `riva_*` and `pm_*` table sets
- [ ] `scripts/migrate_product_db.py` exists and is runnable standalone
- [ ] Migration script run against a copy of product.db produces correct row counts
- [ ] `.venv/bin/pytest tests/ -x --tb=short -q` passes (132+ tests, all green)
- [ ] `product.db` is NOT deleted or modified

---

## Unknowns and Assumptions Requiring Validation

1. **`pm_research.date` type.** In `product.db`, `date` is TEXT with no constraint. Current
   values appear to be ISO date strings (`2026-03-20`). Plan assumes TEXT is correct. Verify
   before adding any `CHECK` constraint.

2. **Act ID FK scope.** `pm_epics.act_id` is treated as a soft reference (no FK constraint),
   matching the existing `riva_projects.act_id` pattern. Confirm this is correct if Cairn's
   Play tables are ever merged into the same DB file.

3. **Whether `query.sh` needs updating.** The `query.sh` script points at `product.db`.
   After migration, the user may want a new `pm_query.sh` targeting `talkingrock.db`. This is
   out of scope but should be tracked.

4. **`pm_issues.cycle_id` source data.** All 24 existing issues have NULL `cycle_id` in
   `product.db`. The schema still carries the column and the FK; this assumption is safe.
   Confirm before migration if any issues were manually added to a cycle.

---

## Confidence Assessment

**High confidence** in Steps 1, 4, 5 (schema, models, errors) — these follow established
patterns with no ambiguity.

**High confidence** in Step 3 (pm_store) — the contract_store and properties_store patterns are
clear and consistent. The main implementation decision (column allowlists in update functions)
is documented.

**Medium confidence** in Step 2 (migration script) — the data shape is fully known, ID remapping
is deterministic, but the script has not been tested against a live DB. The implementer should
run it against a copy first.

**High confidence** in Steps 6–8 (tests) — test fixture pattern is well-established in the
codebase; no novel testing infrastructure is needed.
