# Plan: Git Commit Tracking + DB-Backed Session Memory

## Context

### What exists today

The product management database at `~/talking-rock/product/db/product.db` has five tables:
`epics`, `issues`, `cycles`, `cycle_issues`, `research`, and `roadmap`. The `issues` table
already has `branch` and `forgejo_link` columns but no commit-level tracking. The `cycles`
table has `goal` and `retrospective` but no project or directory fields — so a cycle today
has no machine-readable link to which git repo was active.

Claude Code session management currently works through two hooks:
- `SessionStart` → `session-checkpoint.sh` creates a git tag and injects safety rules.
  It reads nothing from the DB. It writes nothing to the DB.
- `Stop` → `push-commit.sh` auto-commits and pushes. It reads nothing from the DB.
  It writes nothing to the DB. Commit messages are heuristic (`feat(scope): update N file(s)`).

There are no active git hooks in any project repo (only `.sample` files exist in
`/home/kellogg/dev/RIVA/.git/hooks/` and `/home/kellogg/dev/Cairn/.git/hooks/`).

The file-based memory system (`/home/kellogg/.claude/projects/-home-kellogg-dev/memory/MEMORY.md`
and `sessions.md`) serves as a manual backup and cross-session index. It is not structured data.

### Why this change is needed

1. Commit messages produced by `push-commit.sh` carry no semantic meaning — they don't say
   which issue was addressed, which cycle the work belonged to, or what project it affected.
2. Session start has no DB query — Claude gets no structured "where we left off" context from
   the DB and must rely on memory files.
3. There is no way to query "what commits were made during cycle 3?" or "what work touched
   issue 12?" because commits are not linked to the DB at all.
4. The `cycles` table is populated manually or not at all — only 2 cycles exist despite many
   working sessions recorded in `sessions.md`.

---

## Approach (Recommended)

### Design principle

Use two integration points that already exist in the hook architecture:
1. **`SessionStart`** — extend `session-checkpoint.sh` to open a cycle in the DB.
2. **`Stop`** — extend `push-commit.sh` to write the commit hash to the DB and close the cycle.
3. **Git `post-commit` hook** installed per-repo (via a shared installer script) — records each
   individual commit to the DB immediately when it happens, not only at session end.

This layered approach means: the cycle captures the session boundary, individual commits are
captured in real time, and `Stop` closes the loop. No single hook failure loses data.

A thin shared library (`~/.claude/hooks/db-ops.sh`) contains all SQLite operations so logic
is not duplicated across hooks.

### What this is not

This plan does not replace the existing safety hooks. It layers DB writes on top of them.
It does not add new Claude Code hook event types — it extends the two hooks that already fire
at session boundaries.

---

## Alternatives Considered

### Alternative A: Claude Code hooks only (no git hooks)

Use only `SessionStart` and `Stop` to capture the session start/end commit hashes. No
`post-commit` git hook. The `Stop` hook would diff HEAD before/after to find all commits
made during the session.

**Trade-off:** Simpler to install (no per-repo hook setup). But commits made manually in a
terminal or by other tools during the session are invisible until `Stop` fires. The commit→issue
link relies on `Stop` introspecting git log, which is brittle if multiple issues were addressed
in one session. Each commit should be recorded when it happens, not reconstructed later.

**Set aside because:** A per-repo `post-commit` hook is a one-time install and gives real-time
accuracy. The installer script makes it low-friction across all 14+ repos.

### Alternative B: Forgejo webhook → DB bridge

Instead of git hooks, use Forgejo's push webhook to call a local HTTP endpoint that writes to
the DB.

**Trade-off:** Real-time, works even for manual pushes from outside Claude Code sessions.
But requires a persistent local HTTP service, Docker network complexity (the Tailscale/Docker
gotcha is already documented), and fails silently when offline.

**Set aside because:** Requires infrastructure that doesn't exist yet and violates the
local-first, simple-SQLite-shell-scripts principle. The git hook approach works fully offline
and needs no running service.

### Alternative C (Recommended): Layered hooks — Claude hooks + git post-commit

- `SessionStart` opens a cycle row.
- Per-repo `post-commit` records every commit immediately.
- `Stop` closes the cycle, adds retrospective, and backfills any commits that pre-date
  the git hook installation.

This is the most accurate and requires no new infrastructure. The `post-commit` hook is
a 10-line shell script using the same SQLite calls as everything else.

---

## Schema Changes Required

### New table: `commits`

```sql
CREATE TABLE commits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash        TEXT NOT NULL UNIQUE,
    short_hash  TEXT NOT NULL,
    message     TEXT,
    author      TEXT,
    timestamp   TEXT,
    project     TEXT,
    branch      TEXT,
    issue_id    INTEGER REFERENCES issues(id),
    cycle_id    INTEGER REFERENCES cycles(id),
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_commits_issue_id  ON commits(issue_id);
CREATE INDEX idx_commits_cycle_id  ON commits(cycle_id);
CREATE INDEX idx_commits_project   ON commits(project);
CREATE INDEX idx_commits_hash      ON commits(hash);
```

Rationale for `project` and `branch` as plain TEXT (not FK): the project name may not
match an epic's `project` column exactly, and branches come and go. Denormalizing these
two fields keeps queries simple and avoids join complexity for what is essentially a log.

### Modified table: `cycles`

Add two columns:

```sql
ALTER TABLE cycles ADD COLUMN project TEXT;
ALTER TABLE cycles ADD COLUMN project_dir TEXT;
```

`project` is the human name (e.g., "RIVA", "Cairn"). `project_dir` is the absolute path
(e.g., `/home/kellogg/dev/RIVA`). This allows the session-start query to filter cycles
by the current working directory, and allows querying "all cycles for RIVA".

No changes to `epics`, `issues`, `research`, or `roadmap` tables. The `issues.branch`
column already exists for the issue→branch link. The `commits.issue_id` column provides
the commit→issue link.

---

## Hook Architecture

```
Claude Code (SessionStart)
    └─→ session-checkpoint.sh (extended)
          • Creates git checkpoint tag (existing behavior, unchanged)
          • Reads DB: active/in-progress issues for this project
          • Reads DB: last cycle for this project (goal, retrospective)
          • INSERTs a new cycle row (status=Active, project, project_dir, start_date=now)
          • Writes cycle_id to ~/.claude/hooks/state/current-cycle-id
          • Outputs structured context to stdout (Claude sees this as system context)

Individual commit (git post-commit hook, installed per repo)
    └─→ ~/.local/share/claude-db-hooks/post-commit.sh
          • Reads cycle_id from ~/.claude/hooks/state/current-cycle-id
          • Reads commit hash, message, author, branch from git
          • Extracts issue_id from commit message if pattern found (e.g. "fixes #12")
          • INSERTs row into commits table
          • INSERTs row into cycle_issues if not already present

Claude Code (Stop)
    └─→ push-commit.sh (extended)
          • Existing doc-check + auto-commit behavior runs first (unchanged)
          • After commit: reads cycle_id from state file
          • UPDATEs cycles SET status='Complete', end_date=now WHERE id=cycle_id
          • Prompts Claude to write retrospective (via exit 2 bounce, optional)
          • Clears ~/.claude/hooks/state/current-cycle-id
```

### State file

`~/.claude/hooks/state/current-cycle-id` — plain text file containing the integer cycle ID
of the currently open cycle. Written by `SessionStart`, read by `post-commit` and `Stop`,
deleted by `Stop`. One line, one integer.

This avoids environment variable passing (which doesn't survive across tool calls) and avoids
a database query in the hot path of `post-commit`. If the file is missing (session crashed,
first install, manual commit), `post-commit` still inserts the commit but with `cycle_id=NULL`.

### Issue linking via commit message convention

The `post-commit` hook scans the commit message for patterns:

- `fixes #N` → sets `issue_id=N`
- `closes #N` → sets `issue_id=N`
- `refs #N` → sets `issue_id=N`
- `issue #N` → sets `issue_id=N`

If no pattern is found, `issue_id` is NULL. This is intentional — not every commit maps to
an issue. The `git-ops` agent can be instructed to include `refs #N` in commit messages when
working on a tracked issue.

---

## Session Lifecycle (detailed)

### Session Start

1. `session-checkpoint.sh` fires.
2. Determines `project_dir` from `$CLAUDE_PROJECT_DIR` (already done by existing hook).
3. Derives `project` name: basename of `$CLAUDE_PROJECT_DIR` (e.g., `RIVA`).
4. Queries DB:
   - `SELECT id, name, status FROM cycles WHERE project=? AND status='Active' LIMIT 1`
     — if a crashed/unclosed cycle exists, reuse it rather than creating a duplicate.
   - `SELECT id, name, status FROM issues WHERE status IN ('In Progress','Blocked') AND epic_id IN (SELECT id FROM epics WHERE project=?) ORDER BY priority`
   - `SELECT id, name, status IN ('Active') FROM epics WHERE project=?`
   - `SELECT name, key_finding FROM research WHERE project=? ORDER BY date DESC LIMIT 3`
5. INSERTs cycle if none active: `INSERT INTO cycles (name, status, project, project_dir, start_date, goal) VALUES (?, 'Active', ?, ?, datetime('now'), 'Pending')`
6. Writes cycle_id to `~/.claude/hooks/state/current-cycle-id`.
7. Outputs structured context block to stdout:

```
SESSION CONTEXT — [project] @ [timestamp]
Cycle: #[id] [name]

ACTIVE ISSUES:
  #[id] [name] ([status])
  ...

RECENT DECISIONS:
  [key_finding] (from [name])
  ...

[existing checkpoint/safety output follows]
```

### During the session

Each `git commit` (whether made by Claude via `push-commit.sh` or manually) fires the
`post-commit` git hook, which records the commit to the DB with the current cycle_id.

### Session End

`Stop` fires. After the existing doc-check and auto-commit logic:

1. Reads cycle_id from `~/.claude/hooks/state/current-cycle-id`.
2. If file exists:
   - `UPDATE cycles SET status='Complete', end_date=datetime('now') WHERE id=?`
   - Does NOT write a retrospective (retrospective requires LLM-level summarization;
     the hook writes `NULL` and the session CLAUDE.md protocol already instructs Claude
     to write one manually via the `historian` agent).
3. Deletes `~/.claude/hooks/state/current-cycle-id`.

---

## Scripts Needed

### New scripts

| Script | Location | Purpose |
|--------|----------|---------|
| `db-ops.sh` | `~/.claude/hooks/db-ops.sh` | Shared SQLite helper: DB path, project detection, issue parsing |
| `post-commit.sh` | `~/.local/share/claude-db-hooks/post-commit.sh` | Per-repo git post-commit handler, symlinked into each repo |
| `install-git-hooks.sh` | `~/talking-rock/product/scripts/install-git-hooks.sh` | Installer: walks `/home/kellogg/dev/`, symlinks post-commit into each repo |
| `session-context.sh` | `~/.claude/hooks/session-context.sh` | DB query logic for session start (called by session-checkpoint.sh) |
| `migrate-commits.sh` | `~/talking-rock/product/scripts/migrate-commits.sh` | One-time: walk git log in all repos, backfill `commits` table |

### Modified scripts

| Script | Change |
|--------|--------|
| `~/.claude/hooks/session-checkpoint.sh` | Source `session-context.sh` at the end; prepend DB context to stdout output |
| `~/.claude/hooks/push-commit.sh` | After successful commit: call `db-ops.sh record_commit` and `db-ops.sh close_cycle` |

---

## Files Affected

### New files

- `~/.claude/hooks/db-ops.sh`
- `~/.claude/hooks/session-context.sh`
- `~/.claude/hooks/state/` (directory, gitignored)
- `~/.local/share/claude-db-hooks/post-commit.sh`
- `~/talking-rock/product/scripts/install-git-hooks.sh`
- `~/talking-rock/product/scripts/migrate-commits.sh`

### Modified files

- `~/.claude/hooks/session-checkpoint.sh` — source `session-context.sh`, output DB context
- `~/.claude/hooks/push-commit.sh` — add DB writes after successful commit
- `~/talking-rock/product/db/product.db` — schema migration (new `commits` table, two columns on `cycles`)
- `/home/kellogg/.claude/CLAUDE.md` — update Session Protocol section to reference DB cycle auto-creation

### Symlinks (per repo, created by installer)

- `/home/kellogg/dev/*/\.git/hooks/post-commit` → `~/.local/share/claude-db-hooks/post-commit.sh`

Git hooks in `.git/` are not tracked by git — they are local to each clone. The symlink
approach means maintaining one canonical script rather than N copies.

---

## Migration Path

### Phase 1: Schema (no behavior change)

1. Run the `ALTER TABLE` statements and `CREATE TABLE commits` against the live DB.
2. Verify schema with `.schema`. No existing queries break — new columns are nullable.

### Phase 2: Shared library

1. Write `db-ops.sh` with functions: `get_db_path`, `get_project_name`, `open_cycle`,
   `close_cycle`, `record_commit`, `extract_issue_id`.
2. Write `session-context.sh` with the DB query block.
3. Unit-test both scripts by running them manually with a test DB.

### Phase 3: Extend session-checkpoint.sh

1. Add `source ~/.claude/hooks/session-context.sh` at the end of `session-checkpoint.sh`.
2. Test by opening a new Claude Code session in a project dir. Verify: cycle row created in DB,
   state file written, context block appears in session output.
3. The existing checkpoint behavior (git tag, safety rules) is unchanged — extension is additive.

### Phase 4: Extend push-commit.sh

1. Add DB write calls after the `git commit` succeeds (line 255 in current file).
2. Add cycle close call after successful push.
3. Test: make a commit, verify `commits` row appears in DB.

### Phase 5: Git post-commit hook

1. Write `post-commit.sh`.
2. Write `install-git-hooks.sh` — walks `/home/kellogg/dev/`, finds git repos,
   symlinks the hook, skips repos that already have a non-sample `post-commit`.
3. Run installer. Verify hooks are in place in Cairn, RIVA, ReOS.

### Phase 6: Backfill migration (optional, one-time)

1. Run `migrate-commits.sh`: for each repo, `git log --format="%H %h %s %ae %ai"`,
   insert each commit into `commits` table with `cycle_id=NULL`, `issue_id` extracted
   from message.
2. This gives historical commit data in the DB without linking to cycles (those cycles
   don't exist retroactively).

### Phase 7: Update CLAUDE.md

Remove the manual "Session Start" DB query steps (the hook now does them automatically)
and replace with a note that the context is auto-injected. Update "Session End" to note
that cycle close is automatic but retrospective still requires the `historian` agent.

---

## Risks and Edge Cases

### Risk 1: Multiple sessions open simultaneously (different projects)

The state file `current-cycle-id` is a single global file. If two Claude Code sessions
run concurrently (e.g., one in `/dev/RIVA` and one in `/dev/Cairn`), `Stop` for the
first session to close will overwrite/delete the state file before the second session's
`Stop` fires.

**Mitigation:** Use a per-project state file:
`~/.claude/hooks/state/cycle-{project_name}.id` instead of a single file.
`post-commit` identifies the project from the git repo's directory name and reads the
correct file. This also makes debugging easier.

### Risk 2: Session crash (Claude killed, power loss)

If the session ends without `Stop` firing, the cycle row remains `status='Active'` with
no `end_date`. The state file is not cleaned up.

**Mitigation:** `SessionStart` already handles this — step 4 checks for an existing active
cycle for the project and reuses it rather than creating a duplicate. The next session
closes the previous cycle cleanly. This is the correct behavior: the "session" conceptually
continues.

### Risk 3: push-commit.sh commits before post-commit hook sees it

`push-commit.sh` calls `git commit`, which fires the `post-commit` git hook. The hook
writes to the DB. Then `push-commit.sh` continues and also tries to write the same commit.
This is a double-write.

**Mitigation:** `push-commit.sh` should NOT write the commit itself — that's the `post-commit`
hook's job. `push-commit.sh` should only call `close_cycle` after `git push` succeeds.
The `commits` table has `UNIQUE` on `hash`, so double-insert fails gracefully with `INSERT OR IGNORE`.

### Risk 4: Commit in a repo with no post-commit hook installed

Developer makes a commit before running the installer, or adds a new repo without re-running it.

**Mitigation:** `push-commit.sh` calls `db-ops.sh record_commit` as a fallback after every
commit it makes. This catches all Claude-made commits. Manual commits in repos without the hook
go unrecorded until the installer is run, which is acceptable — the system degrades gracefully.

### Risk 5: Commit message parsing is too aggressive

The pattern `refs #N` might match legitimate non-issue references (e.g., `refs/heads/main #12`
in a message about git refs).

**Mitigation:** Anchor the pattern: `\b(fixes|closes|refs|issue)\s+#([0-9]+)\b`. Require
the hash-number to be a valid issue ID: validate `SELECT id FROM issues WHERE id=?` before
writing. If no match, `issue_id=NULL`.

### Risk 6: SQLite write contention

`post-commit` and `push-commit.sh` both write to the DB. If a session has rapid commits,
two writes might overlap.

**Mitigation:** SQLite WAL mode is already the project standard (`PRAGMA journal_mode=WAL`).
WAL allows concurrent readers with one writer. Since `post-commit` is called sequentially
after each commit (not in parallel), write contention is extremely unlikely. Add
`PRAGMA busy_timeout=5000` (5-second wait) to `db-ops.sh` as a defensive measure.

### Risk 7: Session context output is too noisy

If a project has 40 backlog issues, the `SessionStart` context dump makes the system prompt
very long.

**Mitigation:** Query only `status IN ('In Progress', 'Blocked')` for issues, not all issues.
Cap research findings at 3. Cap issue display at 10. The goal is orientation, not a full dump.

---

## Testing Strategy

### Unit tests (manual shell testing)

For each new script, test against a test database (copy of the real DB or a minimal fixture):

1. `db-ops.sh open_cycle` — creates a row, state file written, existing active cycle reused.
2. `db-ops.sh close_cycle` — row updated, state file removed.
3. `db-ops.sh record_commit` — row inserted, deduplication works (`INSERT OR IGNORE`).
4. `db-ops.sh extract_issue_id` — parses "fixes #5", "closes #12", "refs #99", no match → NULL.
5. `session-context.sh` — outputs correct text for a project with known issues/cycles.

### Integration tests

1. Open a Claude Code session in `/dev/RIVA` → confirm cycle row created in DB.
2. Make a commit with `fixes #24` in message → confirm `commits` row with `issue_id=24`.
3. Close session → confirm cycle row has `status='Complete'` and `end_date` set.
4. Open second session in same dir → confirm existing closed cycle not reused (new cycle created).
5. Simulate crash: manually delete state file → open new session → confirm graceful recovery.

### Regression test

After extending `session-checkpoint.sh` and `push-commit.sh`, run through the existing safety
hook test scenarios (deletion-guard, secrets-guard) to confirm behavior is unchanged.

---

## Definition of Done

- [ ] `commits` table created in DB with correct indexes
- [ ] `cycles.project` and `cycles.project_dir` columns added
- [ ] `db-ops.sh` written with: `open_cycle`, `close_cycle`, `record_commit`, `extract_issue_id`
- [ ] `session-context.sh` written and outputs correct context block for RIVA and Cairn
- [ ] `session-checkpoint.sh` extended, backward-compatible, tested in live session
- [ ] `push-commit.sh` extended to call `close_cycle` after successful push
- [ ] `post-commit.sh` written and installed in at minimum: RIVA, Cairn, ReOS
- [ ] `install-git-hooks.sh` written and tested against a fresh repo clone
- [ ] Per-project state files (not single global file) implemented
- [ ] Double-write race condition resolved (`INSERT OR IGNORE` + push-commit does not duplicate)
- [ ] `migrate-commits.sh` run against all repos; verify row count reasonable
- [ ] CLAUDE.md Session Protocol section updated
- [ ] MEMORY.md updated to note that cycles are now auto-created
- [ ] Two real working sessions validated: context appears at start, cycle closed at end

---

## Confidence Assessment

**High confidence (well-understood):**
- Schema changes are additive; no existing queries break.
- The `CLAUDE_PROJECT_DIR` environment variable is available in all hooks (confirmed from
  existing hook code).
- SQLite WAL mode handles the concurrency case.
- Git `post-commit` hook fires reliably after every commit including `git commit` called
  from scripts.

**Medium confidence (requires validation):**
- `SessionStart` hook stdout appears in Claude's system prompt as `additionalContext`.
  This is what existing `session-checkpoint.sh` relies on, but the exact rendering and
  length limits in Claude's context window are not documented in the Anthropic hooks docs.
  If context is too long or truncated, the DB query output may be partially invisible.
- Whether `Stop` fires when Claude Code is killed (Ctrl+C, process kill). If it does not,
  the cycle close must rely on the "reuse active cycle" recovery path in the next session.

**Assumption to validate before implementation:**
- Confirm that `Stop` fires on normal session end (not just on explicit `/exit`).
  The `push-commit.sh` hook already relies on this — if it were broken, auto-commits
  would not be happening. Treat this as validated.
- Confirm `CLAUDE_PROJECT_DIR` is set when opening a session in any project directory,
  not only when a project-level `.claude/` exists. (The global `~/.claude/settings.json`
  is the hook source, not a project-level one, so this needs verification for projects
  that have no `.claude/` dir.)

---

## Appendix: Key File Paths

| File | Role |
|------|------|
| `~/talking-rock/product/db/product.db` | The database |
| `~/.claude/settings.json` | Hook wiring (SessionStart, Stop, PreToolUse) |
| `~/.claude/hooks/session-checkpoint.sh` | SessionStart hook (extend, do not replace) |
| `~/.claude/hooks/push-commit.sh` | Stop hook (extend after line 255) |
| `~/.claude/hooks/db-ops.sh` | New: shared DB operations |
| `~/.claude/hooks/session-context.sh` | New: DB query block for session start |
| `~/.claude/hooks/state/cycle-{project}.id` | New: per-project active cycle ID |
| `~/.local/share/claude-db-hooks/post-commit.sh` | New: git hook target (symlinked into repos) |
| `~/talking-rock/product/scripts/install-git-hooks.sh` | New: hook installer |
| `~/talking-rock/product/scripts/migrate-commits.sh` | New: one-time backfill |
