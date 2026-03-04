# Plan: RIVA as a Standalone Project with Textual TUI

## Context

RIVA (Recursive Intention-Verification Architecture) is currently an archive extracted from
Cairn. It lives at `/home/kellogg/dev/RIVA/` with:

- `src/code_mode/` — 45 source files, 16 optimization files (~22K LOC), no `__init__` at the
  `src/` level, no `pyproject.toml`
- `tests/` — 35 test files (~12.5K LOC), all importing from `reos.*`

The codebase has exactly two non-`code_mode` import dependencies:
```
from reos.config import TIMEOUTS, EXECUTION     # executor.py
from reos.security import is_command_safe, verify_command_safety_llm  # sandbox.py
```

And two Cairn `play_fs` / `providers` TYPE_CHECKING references:
```
from reos.play_fs import Act        # router.py, factory.py, repo_analyzer.py (TYPE_CHECKING only)
from reos.providers import LLMProvider  # many files (TYPE_CHECKING only)
from reos.db import Database        # factory.py, project_memory.py (TYPE_CHECKING only)
```

The Tauri-specific TypeScript (`codeModeView.ts`, `diffPreviewOverlay.ts`) is not reusable and
will be superseded by the Textual TUI.

**Why this work is needed:** RIVA is the most architecturally sophisticated piece of the Talking
Rock ecosystem. As an archive it cannot be developed, tested independently, or distributed. Making
it standalone enables focused iteration, a dedicated UX, and potential open-source release.

---

## Approach (Recommended): Package-First, TUI-Second

Extract RIVA as a proper Python package in two clean phases. Phase 1-2 are purely backend: rename
imports, stand up the package, make tests pass. Phases 3-5 build the TUI progressively on top of
the working backend.

### Why This Approach

The alternative — doing package extraction and TUI simultaneously — means debugging import errors
while also debugging Textual widget behavior. Separating them preserves clean checkpoints and makes
failures easier to diagnose.

### Shared Core Package: `talkingrock-core`

The `talkingrock-core` package extracts the small set of Cairn modules that RIVA legitimately
needs. Based on the import audit, RIVA needs exactly:

1. `LLMProvider` protocol and `OllamaProvider` implementation — used everywhere (providers/)
2. `is_command_safe`, `verify_command_safety_llm` — used in `sandbox.py`
3. `TIMEOUTS`, `EXECUTION` config constants — used in `executor.py`
4. `Database` — used in `project_memory.py` and `factory.py`

The `Act` references in `router.py`, `factory.py`, and `repo_analyzer.py` are all under
`TYPE_CHECKING` or represent Cairn's concept of a "project with a repo." In standalone RIVA, the
`Act` concept collapses to a simple `Project` dataclass that RIVA owns directly.

**`talkingrock-core` contents (minimal viable):**
```
talkingrock-core/
  src/talkingrock/
    __init__.py
    providers/
      __init__.py
      base.py          # LLMProvider protocol, ModelInfo, ProviderHealth
      ollama.py        # OllamaProvider
      factory.py       # get_provider(), get_provider_or_none()
    db.py              # Database class (sqlite3 wrapper, WAL mode)
    errors.py          # LLMError, ValidationError, DatabaseError
    config.py          # TIMEOUTS, EXECUTION, SECURITY constants
    security.py        # is_command_safe(), verify_command_safety_llm()
  pyproject.toml
```

This is a thin extraction of existing code from `src/cairn/`. The modules already exist; they
just need to be copied with import paths updated from `cairn.*` to `talkingrock.*`.

**Note:** `talkingrock-core` is referenced here as the contract RIVA depends on. The actual
`talkingrock-core` package extraction is a separate implementation task (likely planned alongside
the ReOS standalone plan). This plan assumes `talkingrock-core` exists by the time Phase 2 begins,
or that RIVA vendors those few modules temporarily during Phase 1.

---

## Alternatives Considered

### Alternative A: Vendor Everything Internally

Copy `config.py`, `security.py`, `providers/`, and `db.py` directly into the RIVA package as
`riva/core/`. No shared package, no cross-project dependency.

**Trade-offs:**
- Pro: Fully standalone with zero external deps from the Talking Rock ecosystem
- Pro: Can be open-sourced independently without pulling in Cairn code
- Con: Triplicates the shared logic (Cairn, ReOS, RIVA each have their own copy)
- Con: Bug fixes in the provider layer must be applied three places

The user specified "shares common libs with Cairn and ReOS" — this approach contradicts that
requirement directly. Noted for completeness; not recommended.

### Alternative B: Build Textual TUI First, Extract Package Later

Start with the TUI scaffolding using the archive in-place, then migrate imports.

**Trade-offs:**
- Pro: Immediate visual progress, easier to show stakeholders
- Con: Debugging TUI behavior against broken imports is double the pain
- Con: The `ExecutionObserver` pattern in `streaming.py` was designed for Tauri polling — the TUI
  integration model is fundamentally different (reactive push, not periodic poll). Discovering
  this coupling while fighting import errors costs significant time.

Not recommended for the same "certainty before action" reason that underlies the recommended
approach.

---

## Implementation Steps

### Phase 1: Package Scaffolding and Import Migration

**Goal:** `import riva` works; all 35 test files run (passing or explicitly skipped for missing
deps).

#### Step 1.1 — Create package structure

Create `/home/kellogg/dev/RIVA/pyproject.toml` and the `src/riva/` package tree:

```
/home/kellogg/dev/RIVA/
  pyproject.toml
  src/
    riva/
      __init__.py          # Public API re-exports
      contract.py          # (moved from code_mode/contract.py)
      diff_utils.py
      executor.py
      explorer.py
      intent.py
      intent_to_nol.py
      intention.py
      json_utils.py
      nol_bridge.py
      perspectives.py
      planner.py
      project_memory.py
      quality.py
      repo_analyzer.py
      router.py
      sandbox.py
      session_logger.py
      streaming.py
      test_generator.py
      tools.py
      web_tools.py
      optimization/
        __init__.py
        complexity.py
        factory.py
        fast_path.py
        metrics.py
        model_selector.py
        pattern_success.py
        risk.py
        semantic_validator.py
        status.py
        trust.py
        verification.py
        verification_layers.py
        parsers/
          __init__.py
          base.py
          javascript_parser.py
          python_parser.py
  tests/                   # test files moved here from RIVA/tests/
    conftest.py
    test_*.py              # all 35 existing files
```

The `code_mode` name is a Cairn artifact. In standalone RIVA, the top-level package is `riva`.

#### Step 1.2 — Define import mapping and perform mechanical substitution

Every import in every source file and test file follows this deterministic mapping:

| Old import | New import |
|---|---|
| `from reos.code_mode.X import Y` | `from riva.X import Y` |
| `from reos.code_mode.optimization.X import Y` | `from riva.optimization.X import Y` |
| `from reos.config import TIMEOUTS, EXECUTION` | `from talkingrock.config import TIMEOUTS, EXECUTION` |
| `from reos.security import is_command_safe, verify_command_safety_llm` | `from talkingrock.security import is_command_safe, verify_command_safety_llm` |
| `from reos.providers import LLMProvider` | `from talkingrock.providers import LLMProvider` |
| `from reos.providers.factory import get_provider` | `from talkingrock.providers.factory import get_provider` |
| `from reos.db import Database` | `from talkingrock.db import Database` |
| `from reos.play_fs import Act` | `from riva.project import Project` (see Step 1.3) |

This is a mechanical search-and-replace. Use `sed -i` or a script. After the replace, run
`grep -r "reos\." src/ tests/` to verify zero remaining references.

#### Step 1.3 — Replace `Act` with `Project`

The `Act` type from `reos.play_fs` appears in:
- `router.py` — `active_act: Act | None` parameter
- `factory.py` — `act: Act` parameter to `analyze_repo_and_populate_memory()`
- `repo_analyzer.py` — `act: Act` parameter throughout

In standalone RIVA, there is no "Play" system. The relevant fields of `Act` used in RIVA are:
```python
act.repo_path   # str | None — the project's repository root
act.title       # str — project display name
act.artifact_type  # str | None — e.g., "python"
```

Create `/home/kellogg/dev/RIVA/src/riva/project.py`:
```python
@dataclass
class Project:
    """A software project RIVA can work on."""
    repo_path: str
    title: str = ""
    artifact_type: str | None = None  # "python", "javascript", etc.
```

Replace every `Act` reference in `router.py`, `factory.py`, and `repo_analyzer.py` with
`Project`. This is a shallow change — no behavior changes, only the type name.

#### Step 1.4 — Temporarily vendor `talkingrock-core` dependencies

If `talkingrock-core` does not yet exist as an installable package, create a
`src/riva/_vendor/` directory containing copies of the four needed modules from Cairn:
```
src/riva/_vendor/
  __init__.py
  config.py      # copied from src/cairn/config.py, imports changed
  security.py    # copied from src/cairn/security.py, imports changed
  providers/     # copied from src/cairn/providers/
  db.py          # copied from src/cairn/db.py
  errors.py      # copied from src/cairn/errors.py
```

Import path in this interim state: `from riva._vendor.config import TIMEOUTS`.
When `talkingrock-core` is published, replace `_vendor` imports with `talkingrock.*`.

#### Step 1.5 — Write `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "riva"
version = "0.1.0a0"
description = "RIVA — Recursive Intention-Verification Architecture"
authors = [{ name = "Talking Rock" }]
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27.0,<1.0.0",       # Ollama client
  "tenacity>=8.2.0,<10.0.0",    # Retry with backoff
  # talkingrock-core will go here when published
]

[project.optional-dependencies]
dev = [
  "ruff>=0.6.0,<0.8.0",
  "mypy>=1.11.0,<1.13.0",
  "pytest>=8.3.0,<9.0.0",
  "pytest-cov>=4.1.0,<6.0.0",
  "pytest-asyncio>=0.23.0,<1.0.0",
]
tui = [
  "textual>=0.80.0,<1.0.0",     # TUI framework
  "rich>=13.0.0,<14.0.0",       # Rich text rendering (Textual dep)
]
parsing = [
  "tree-sitter>=0.23.0,<1.0.0",
  "tree-sitter-python>=0.23.0,<1.0.0",
  "tree-sitter-javascript>=0.23.0,<1.0.0",
]
db-crypto = [
  "pysqlcipher3>=1.2.0,<2.0.0",  # SQLCipher (requires libsqlcipher-dev)
]

[project.scripts]
riva = "riva.__main__:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=riva --cov-report=term-missing -m 'not slow'"
markers = [
    "slow: marks tests that call Ollama for real LLM inference",
]
```

#### Step 1.6 — Create `src/riva/__main__.py` (minimal CLI stub)

```python
"""RIVA CLI entry point."""

def main() -> None:
    print("RIVA v0.1.0 — Recursive Intention-Verification Architecture")
    print("Use: riva run <repo-path> '<intent>'  (Phase 2)")
    print("Use: riva tui                          (Phase 3)")

if __name__ == "__main__":
    main()
```

#### Step 1.7 — Verify test suite runs

```bash
cd /home/kellogg/dev/RIVA
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH="src" python -m pytest tests/ -x --tb=short -q --no-cov
```

**Definition of done for Phase 1:** All 35 test files importable; tests that were passing before
still pass; no `reos.*` imports remain in any file under `src/` or `tests/`.

---

### Phase 2: Core RIVA Functionality (CLI Entry Point)

**Goal:** A user can run `riva run /path/to/repo "add a factorial function"` from the terminal
and get RIVA's full 7-phase execution loop without any TUI.

#### Step 2.1 — Create `src/riva/session.py`

This is the standalone equivalent of the Tauri RPC entry points. It wires together:
- `OllamaProvider` (from talkingrock-core or `_vendor`)
- `Database` (RIVA's own DB, not Cairn's)
- `ProjectMemoryStore`
- `CodeSandbox`
- `CodeExecutor`
- `ExecutionObserver` → console output

```python
@dataclass
class RIVASession:
    """A single RIVA coding session."""
    project: Project
    llm: LLMProvider
    db: Database
    sandbox: CodeSandbox
    executor: CodeExecutor
    memory: ProjectMemoryStore | None = None

def create_session(repo_path: str, ollama_url: str | None = None) -> RIVASession:
    """Create a new RIVA session for a repository."""
    ...

async def run_intent(session: RIVASession, intent: str) -> ExecutionResult:
    """Execute a coding intent and return the result."""
    ...
```

#### Step 2.2 — Create console `ExecutionObserver`

Replace the Tauri-targeted observer with a `ConsoleObserver` that prints to stdout:

```python
class ConsoleObserver(ExecutionObserver):
    """Observer that prints execution state to the console."""
    def on_phase_change(self, status: LoopStatus) -> None:
        print(f"\n[{status.value.upper()}] {PHASE_INFO[status.value][1]}")
    def on_step_start(self, step: ContractStep) -> None:
        print(f"  -> {step.description}")
    # etc.
```

This observer is reused by the TUI — the TUI replaces it with a reactive widget update, but the
interface is identical.

#### Step 2.3 — Expand `__main__.py` with Click CLI

```python
@click.group()
def cli(): ...

@cli.command()
@click.argument("repo_path")
@click.argument("intent")
@click.option("--model", default=None, help="Ollama model name")
@click.option("--max-iterations", default=10)
def run(repo_path: str, intent: str, model: str | None, max_iterations: int):
    """Run a coding intent against a repository."""
    session = create_session(repo_path)
    result = asyncio.run(run_intent(session, intent))
    if result.success:
        click.echo(f"\nSuccess: {result.message}")
    else:
        click.echo(f"\nFailed: {result.message}")
        sys.exit(1)
```

Add `click>=8.0.0,<9.0.0` to `pyproject.toml` dependencies (not optional — CLI is core).

#### Step 2.4 — Create `src/riva/db_schema.py`

RIVA's standalone database schema. Separate from Cairn's schema. Tables needed:

```sql
-- Project memory (from project_memory.py)
CREATE TABLE project_decisions (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT,
    scope TEXT DEFAULT 'global',
    keywords TEXT,          -- JSON array
    source TEXT,
    confidence REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    superseded_by TEXT
);

CREATE TABLE project_patterns (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    pattern_type TEXT NOT NULL,
    description TEXT NOT NULL,
    applies_to TEXT,
    example_code TEXT,
    source TEXT,
    occurrence_count INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE user_corrections (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    original_code TEXT,
    corrected_code TEXT,
    correction_type TEXT,
    file_path TEXT,
    session_id TEXT,
    keywords TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE coding_sessions (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    prompt TEXT NOT NULL,
    outcome TEXT,
    files_changed TEXT,     -- JSON array
    duration_seconds INTEGER,
    iterations INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- Session logs (from session_logger.py)
CREATE TABLE session_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    module TEXT,
    action TEXT,
    message TEXT,
    data TEXT               -- JSON
);

-- Execution metrics (from optimization/metrics.py)
CREATE TABLE execution_metrics (
    session_id TEXT PRIMARY KEY,
    repo_path TEXT,
    started_at TEXT,
    completed_at TEXT,
    total_duration_ms INTEGER DEFAULT 0,
    llm_calls_total INTEGER DEFAULT 0,
    decomposition_count INTEGER DEFAULT 0,
    success INTEGER DEFAULT 0,  -- boolean
    fast_path_used INTEGER DEFAULT 0,
    data TEXT                   -- JSON for extended fields
);
```

Database file location: `~/.riva-data/riva.db` (configurable via `RIVA_DATA_DIR` env var).

#### Step 2.5 — Settings

Create `src/riva/settings.py`:

```python
@dataclass
class RIVASettings:
    data_dir: Path = Path.home() / ".riva-data"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"  # good default for code tasks
    default_verification_strategy: str = "STANDARD"
    default_max_iterations: int = 10
    trust_budget_initial: float = 100.0
    session_log_retention_days: int = 30

settings = RIVASettings()  # singleton, loaded from ~/.riva-data/settings.json
```

**Definition of done for Phase 2:** `riva run /tmp/test-repo "add a hello function to main.py"`
completes the 7-phase loop, writes the file, prints output to console. All existing tests still
pass.

---

### Phase 3: TUI — Session View and Diff Preview

**Goal:** `riva tui` launches a Textual app. The user can type an intent, watch 7-phase execution
live, and preview/apply diffs.

#### Architecture decision: Observer pattern bridges RIVA to Textual

The `ExecutionObserver` in `streaming.py` is already a clean observer interface with callbacks for
every meaningful event. The TUI `RIVAApp` creates a `TextualObserver` that, instead of printing
to console, calls `self.app.call_from_thread(widget.update, ...)` to post reactive updates.

This is the critical architectural decision for Phase 3. The `CodeExecutionContext` threading
model (background thread + polling) was designed for Tauri RPC and should NOT be reused. Instead:

- RIVA executes in a Textual Worker (background thread managed by Textual)
- The `TextualObserver` uses Textual's `post_message()` from the worker thread to post
  `ExecutionEvent` messages to the app
- The app reacts to those messages to update widgets

#### Step 3.1 — Create `src/riva/tui/` package

```
src/riva/tui/
  __init__.py
  app.py           # RIVAApp (Textual App subclass)
  screens/
    __init__.py
    session.py     # SessionScreen — main coding session
    diff.py        # DiffScreen — diff preview and apply/reject
  widgets/
    __init__.py
    phase_bar.py   # PhaseProgressBar
    intent_input.py   # IntentInput (styled Input)
    contract_panel.py # ContractPanel
    step_list.py   # StepList
    output_log.py  # OutputLog (rolling log)
    diff_view.py   # DiffFileView
  messages.py      # Textual Message subclasses for RIVA events
  observer.py      # TextualObserver (bridges RIVA → Textual)
  worker.py        # RIVAWorker (Textual Worker that runs execution)
```

#### Step 3.2 — Session Screen layout

```
+------------------------------------------------------------------+
| RIVA  [project: /path/to/repo]       [model: qwen2.5-coder:7b]  |
+------------------------------------------------------------------+
|                                                                  |
|  PHASE: [1][2][3][4][5][6][7]  INTENT > Contract > Build > ...  |
|  ================================================================|
|                                                                  |
|  Intent: ___________________________________________________     |
|           [Enter to submit]  [D] Diff  [C] Contract  [Q] Quit  |
|                                                                  |
|  +---------------------------+  +-----------------------------+  |
|  | STEPS (3/7)               |  | OUTPUT LOG                  |  |
|  |---------------------------|  |-----------------------------|  |
|  | [x] Understand intent     |  | [CONTRACT] Building...      |  |
|  | [x] Define contract       |  |   > Criterion: file exists  |  |
|  | [>] Create calculator.py  |  |   > Criterion: tests pass   |  |
|  | [ ] Write add()           |  | [BUILD] executor.py:42      |  |
|  | [ ] Write tests           |  |   -> Create calculator.py   |  |
|  | [ ] Verify syntax         |  |   checkmark Add add()         |  |
|  | [ ] Verify behavioral     |  | [VERIFY] Syntax: PASS       |  |
|  +---------------------------+  +-----------------------------+  |
|                                                                  |
|  Criteria: 2/4 fulfilled   Files changed: calculator.py         |
|  Iteration: 1/10           Elapsed: 4.2s                        |
+------------------------------------------------------------------+
```

**Widgets:**
- `PhaseProgressBar` — 7 segments, current phase highlighted; uses `PHASE_INFO` from
  `riva.streaming` directly
- `IntentInput` — `Input` widget, submits on Enter, disabled during execution
- `StepList` — `ListView` of steps, each with status icon (pending/in-progress/done/failed)
- `OutputLog` — `RichLog` widget, auto-scrolls, max 200 lines retained
- Phase label bar — plain `Label` showing current phase name and description
- Footer — criteria count, files changed, iteration, elapsed (reactive labels, updated by messages)

**Key interactions:**
- Enter — submit intent (if not executing), confirm approval (if awaiting)
- `d` — push `DiffScreen` (available at any point during or after execution)
- `c` — push `ContractScreen` (available once contract is built)
- `q` — quit (prompts if execution is running)
- `Ctrl+C` — cancel running execution (sets `cancel_event` on worker)
- `y`/`n` — approve/reject when execution is `AWAITING_APPROVAL`

#### Step 3.3 — Diff Preview Screen layout

Ports the concept from `diffPreviewOverlay.ts`.

```
+------------------------------------------------------------------+
| DIFF PREVIEW  [3 files changed: +42 -7]           [ESC] Back    |
+------------------------------------------------------------------+
| Files:                                                           |
|  [A] calculator.py          +21 -0    [Apply] [Skip]            |
|  [M] tests/test_calc.py     +18 -0    [Apply] [Skip]            |
|  [M] __init__.py            +3  -7    [Apply] [Skip]            |
+------------------------------------------------------------------+
| calculator.py                                                    |
+------------------------------------------------------------------+
| @@ -0,0 +1,21 @@                                                |
| + def add(a: float, b: float) -> float:                         |
| +     """Add two numbers."""                                     |
| +     return a + b                                               |
| ...                                                              |
+------------------------------------------------------------------+
| [A] Apply All  [S] Skip All  [Enter] Apply Selected  [ESC] Back |
+------------------------------------------------------------------+
```

**Data source:** `DiffPreviewManager` from `riva.diff_utils` — this class already exists and
manages `FileChange` objects. The screen reads from it directly.

**Widgets:**
- File list — `ListView` of files with `+`/`-` line counts and per-file Apply/Skip buttons
- Diff view — `RichLog` with syntax-highlighted diff lines (green for additions, red for
  deletions, using Rich markup)
- Selecting a file in the list updates the diff view

**Key interactions:**
- Navigate file list with arrows
- `a` — apply selected file's changes (calls `sandbox.write_file` for the new content)
- `s` — skip selected file
- `A` — apply all pending files
- `S` — skip all (abandon execution result)
- ESC — return to Session screen

#### Step 3.4 — `TextualObserver` and `RIVAWorker`

```python
# messages.py
from textual.message import Message

class PhaseChanged(Message):
    def __init__(self, status: str, phase: str, description: str, phase_index: int): ...

class StepStarted(Message):
    def __init__(self, step_id: str, description: str, target_file: str | None): ...

class StepCompleted(Message):
    def __init__(self, step_id: str, success: bool, output: str): ...

class CriterionVerified(Message):
    def __init__(self, criterion_id: str, description: str, verified: bool): ...

class OutputLine(Message):
    def __init__(self, line: str): ...

class ExecutionFinished(Message):
    def __init__(self, success: bool, message: str, files_changed: list[str]): ...

class ApprovalRequired(Message):
    def __init__(self, prompt: str, options: list[str]): ...
```

```python
# observer.py
class TextualObserver(ExecutionObserver):
    def __init__(self, app: "RIVAApp"):
        self._app = app

    def on_phase_change(self, status: LoopStatus) -> None:
        info = PHASE_INFO.get(status.value, (0, "Unknown", ""))
        self._app.post_message(
            PhaseChanged(status.value, info[1], info[2], info[0])
        )

    def on_step_start(self, step: ContractStep) -> None:
        self._app.post_message(StepStarted(step.id, step.description, step.target_file))

    # ... one method per ExecutionObserver callback ...
```

```python
# worker.py
from textual.worker import Worker

async def riva_execution_worker(
    app: "RIVAApp",
    session: RIVASession,
    intent: str,
) -> ExecutionResult:
    observer = TextualObserver(app)
    result = await run_intent(session, intent, observer=observer)
    return result
```

The worker is started via `app.run_worker(riva_execution_worker(app, session, intent))`.

#### Step 3.5 — `RIVAApp` in `app.py`

```python
class RIVAApp(App):
    CSS_PATH = "riva.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "push_screen('diff')", "Diff"),
    ]

    def __init__(self, repo_path: str, ollama_model: str | None = None):
        super().__init__()
        self.session = create_session(repo_path, ollama_model=ollama_model)
        self._current_worker: Worker | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield SessionScreen()
        yield Footer()

    def on_execution_finished(self, message: ExecutionFinished) -> None:
        # Update UI, unlock input
        ...
```

Add to `__main__.py`:
```python
@cli.command()
@click.argument("repo_path", default=".")
@click.option("--model", default=None)
def tui(repo_path: str, model: str | None):
    """Launch the RIVA TUI."""
    from riva.tui.app import RIVAApp
    app = RIVAApp(repo_path=repo_path, ollama_model=model)
    app.run()
```

**Definition of done for Phase 3:** `riva tui /path/to/repo` launches the TUI. The user can type
an intent, watch the 7-phase loop run, see live output, and preview diffs. Apply/skip individual
files works. All Phase 1-2 tests still pass.

---

### Phase 4: Contract View, Project Memory, Settings

**Goal:** Three additional screens accessible from the session view.

#### Contract Screen layout

```
+------------------------------------------------------------------+
| CONTRACT  [Active]  [All 4 criteria pending]       [ESC] Back   |
+------------------------------------------------------------------+
|                                                                  |
| Intent:                                                          |
|   Add a factorial function that handles n=0 and negative inputs |
|                                                                  |
| Assumptions:                                                     |
|   - Python function, not a class                                 |
|   - Place in existing calculator.py                              |
|                                                                  |
| Acceptance Criteria:                                             |
|   [ ] FILE_EXISTS    calculator.py contains factorial function   |
|   [ ] FUNCTION_EXISTS  factorial(n: int) -> int                  |
|   [ ] TESTS_PASS    tests/test_calc.py::test_factorial           |
|   [ ] GENERATED_TEST  test_factorial_zero, test_factorial_neg    |
|                                                                  |
| Steps (7):                                                       |
|   [x] 1. Read current calculator.py                              |
|   [>] 2. Write factorial function                                |
|   [ ] 3. Write tests                                             |
|   ...                                                            |
+------------------------------------------------------------------+
| Contract ID: ctr-a3f9b2c1                          Iteration: 1 |
+------------------------------------------------------------------+
```

**Data source:** `Contract` and `ContractBuilder` from `riva.contract`. The screen receives the
current contract via a `ContractBuilt` message posted by `TextualObserver.on_contract_built()`.

#### Project Memory Screen layout

```
+------------------------------------------------------------------+
| PROJECT MEMORY  [/path/to/repo]  [12 decisions, 8 patterns]     |
+------------------------------------------------------------------+
| [Search: ____________________]   [Decisions] [Patterns] [Fixes] |
+------------------------------------------------------------------+
| DECISIONS                                          [+ New]       |
|------------------------------------------------------------------|
| [1] We use dataclasses, not TypedDict           scope: global    |
|     Source: inferred  Confidence: 95%           2026-02-15      |
|                                                                  |
| [2] All API endpoints return JSON snake_case    scope: module:api|
|     Source: user_explicit  Confidence: 100%     2026-02-10      |
+------------------------------------------------------------------+
| [E] Edit  [D] Delete  [ESC] Back                                 |
+------------------------------------------------------------------+
```

**Data source:** `ProjectMemoryStore` from `riva.project_memory`, queried with the current
`Project.repo_path`.

**Key interactions:**
- Tab between Decisions / Patterns / Corrections tabs
- Type in search box to filter (keyword match)
- `e` on selected item — inline edit modal (text area, save on Enter)
- `d` on selected item — confirm-delete dialog
- `n` — new item form

#### Settings Screen layout

```
+------------------------------------------------------------------+
| SETTINGS                                           [ESC] Back   |
+------------------------------------------------------------------+
|                                                                  |
| LLM Provider                                                     |
|   Ollama URL:    [http://localhost:11434        ]                |
|   Model:         [qwen2.5-coder:7b              ]                |
|   [Test Connection]  Status: checkmark Connected (3 models)       |
|                                                                  |
| Verification                                                     |
|   Default strategy:  ( ) MINIMAL  (x) STANDARD  ( ) THOROUGH   |
|                       ( ) MAXIMUM                                |
|   Max iterations:    [10]                                        |
|   Wall-clock timeout: [300] seconds                              |
|                                                                  |
| Trust Budget                                                     |
|   Initial trust:  [100]  (0-100, higher = less verification)    |
|                                                                  |
| Data                                                             |
|   Data directory:  [~/.riva-data                ]               |
|   Session log retention: [30] days                               |
|   [Open data directory]                                          |
|                                                                  |
+------------------------------------------------------------------+
| [Save]  [Reset to Defaults]  [ESC] Cancel                        |
+------------------------------------------------------------------+
```

**Data source:** `RIVASettings` from `riva.settings`. Changes are written to
`~/.riva-data/settings.json` on Save.

**Verification strategy values** map directly to `VerificationStrategy` enum in
`riva.optimization.verification_layers`.

**Definition of done for Phase 4:** All three screens launch, display data from backend, and
persist changes. Project Memory search works. Settings save and reload on restart.

---

### Phase 5: Session History, Polish, Documentation

**Goal:** Complete feature parity with the specified UX. Production-quality error handling and
edge cases.

#### Session History Screen layout

```
+------------------------------------------------------------------+
| SESSION HISTORY  [/path/to/repo]  [23 sessions]    [ESC] Back   |
+------------------------------------------------------------------+
| [Search: ____________________]  [Filter: All v]                  |
+------------------------------------------------------------------+
| 2026-03-01 14:23  "add factorial function"     SUCCESS  4.2s     |
|   3 files changed  7 steps  4/4 criteria  1 iteration            |
|                                                                   |
| 2026-02-28 11:15  "refactor parser module"     SUCCESS  12.8s    |
|   5 files changed  12 steps  6/6 criteria  2 iterations          |
|                                                                   |
| 2026-02-27 16:44  "add user authentication"    FAILED   45.0s   |
|   0 files changed  3 steps  1/5 criteria  TIMEOUT                |
+------------------------------------------------------------------+
| [Enter] View session detail  [ESC] Back                          |
+------------------------------------------------------------------+
```

**Session Detail sub-screen:**
- Full intent text
- Contract with all criteria and their final status
- All steps with outputs
- Files changed (click to see diff)
- Patterns learned from this session
- Execution metrics (LLM calls, time breakdown)

**Data source:** `coding_sessions` and `session_logs` tables from `riva.db_schema`. The
`SessionLogger` already writes structured entries to disk; Phase 5 moves this to SQLite using the
`session_logs` table.

#### Polish items

**Approval UX — inline keyboard confirmation:**
When `LoopStatus.AWAITING_APPROVAL`, the footer changes to:
```
[Y] Yes, apply this step  [N] No, skip  [A] Apply all remaining  [ESC] Cancel
```
The `TextualObserver.on_approval_required()` posts `ApprovalRequired` message; the session screen
handles it by binding temporary keys and showing a confirmation panel above the output log.

**Batch approval for low-risk actions:** When `ActionRisk.level == LOW` and trust budget > 70, a
`BatchApproval` message offers "Apply all low-risk steps automatically?". `y` sets an
`auto_approve_low_risk` flag on the session.

**Keyboard shortcuts summary** (consistent across all screens):
```
q / ESC    — Quit / Back
d          — Diff Preview (session screen only)
c          — Contract View (session screen only)
m          — Project Memory (any screen)
h          — Session History (any screen)
s          — Settings (any screen)
?          — Help overlay
```

**Error handling edge cases:**
- Ollama not running: startup check with friendly error screen (not a crash)
- Repo path doesn't exist: shown in session header with warning icon
- Execution timeout: displayed as `TIMEOUT` status, partial diff still available
- Database corruption: graceful fallback to no-memory mode with warning

**CSS theme (`riva.tcss`):** Monochrome with one accent color (e.g., `$success: green`,
`$error: red`, `$accent: cyan`). No color bloat. Respects terminal dark/light mode.

**Definition of done for Phase 5:** All 6 screens functional. Error paths handled gracefully.
CSS theme applied. `riva --help` and `riva tui --help` show useful help text.

---

## Data Model

### Storage Location

```
~/.riva-data/               (or $RIVA_DATA_DIR)
  riva.db                   # SQLite — project memory, sessions, metrics, logs
  settings.json             # User settings (non-sensitive)
  session-logs/             # Raw session log files (kept until SQLite migration)
    {session_id}.jsonl      # One log entry per line
```

The `riva.db` path is separate from Cairn's `reos.db`. RIVA has its own schema with no
dependency on Cairn's migrations.

### Database Tables

See Step 2.4 for DDL. Summary:

| Table | Purpose |
|---|---|
| `project_decisions` | Learned project decisions from `ProjectMemoryStore` |
| `project_patterns` | Recurring code patterns |
| `user_corrections` | Changes the user made to AI-generated code |
| `coding_sessions` | History of all RIVA sessions |
| `session_logs` | Structured logs for each session (verbose debug) |
| `execution_metrics` | Timing and LLM call counts per session |

The `ProjectMemoryStore` in `riva.project_memory` already implements the query/insert/update
logic. It just needs its `Database` dependency to point at `~/.riva-data/riva.db` instead of
`~/.reos-data/reos.db`.

### Settings Storage

`~/.riva-data/settings.json` — plain JSON, human-editable. The `RIVASettings` dataclass
(see Step 2.5) serializes/deserializes this file. No keyring required for settings (no secrets
stored here).

---

## Execution Flow in TUI

The complete user journey from intent to verified code:

```
1. User opens TUI:
   riva tui /path/to/my-project

2. SessionScreen appears. Intent input is focused.
   Header shows project path, model name.
   If project has a ProjectMemory, a "Memory: N decisions loaded" badge shows.

3. User types: "add a factorial function with edge case handling"
   Presses Enter.

4. IntentInput is disabled. PhaseBar moves to phase 1 (INTENT).
   RIVAWorker starts in background.

5. Phase 1 — INTENT:
   OutputLog: "[INTENT] Discovering what you truly want..."
   OutputLog: "  > Analyzing prompt..."
   OutputLog: "  > Reading project context (3 files)..."
   OutputLog: "  > Intent: Add a recursive factorial function..."
   StepList is empty.

6. Phase 2 — CONTRACT:
   OutputLog: "[CONTRACT] Defining explicit success criteria..."
   ContractPanel (if visible) begins populating.
   Four criteria appear one by one.
   Footer: "Criteria: 0/4"

7. [Approval gate — if configured for manual approval of contract]
   Footer changes to: "[Y] Approve contract  [N] Reject  [C] View contract"
   User presses C to review, then Y to approve.

8. Phase 3 — DECOMPOSE:
   StepList populates with 7 steps.
   Footer: "Steps: 0/7"

9. Phase 4 — BUILD (step 1):
   StepList: [>] "Read current calculator.py"
   OutputLog: "  -> Reading calculator.py (84 lines)"
   StepList: [x] "Read current calculator.py" (green checkmark)

10. Phase 4 — BUILD (step 2):
    StepList: [>] "Write factorial function"
    [If high-risk or trust is low, approval is required]
    Footer: "[Y] Apply  [N] Skip  [ESC] Cancel"
    User presses Y.
    OutputLog: "  -> Writing factorial() to calculator.py"
    StepList: [x] "Write factorial function"
    Footer: Files changed: calculator.py

11. Phase 5 — VERIFY:
    PhaseBar moves to phase 5.
    OutputLog: "[VERIFY] Syntax: PASS (1ms)"
    OutputLog: "[VERIFY] Semantic: PASS (12ms)"
    OutputLog: "[VERIFY] Behavioral: running pytest..."
    OutputLog: "  checkmark 1 test passed (test_factorial)"
    Footer: "Criteria: 3/4"

12. Phase 7 — GAP ANALYSIS:
    OutputLog: "[GAP] Checking what remains..."
    OutputLog: "  Remaining: test_factorial_zero not yet written"
    Loop continues to iteration 2.

13. COMPLETED:
    PhaseBar fully lit.
    OutputLog: "checkmark Complete: 4/4 criteria fulfilled"
    Footer: "2 files changed  7 steps  4/4 criteria  2 iterations  8.3s"
    IntentInput re-enabled.
    "Press [D] to preview diff or [Enter] for new task"

14. User presses D:
    DiffScreen pushes onto stack.
    Shows calculator.py (+21 lines) and tests/test_calc.py (+15 lines).
    User reviews, presses A to apply all.
    DiffScreen pops. Back to SessionScreen.
```

---

## Testing Strategy

### Existing Tests (35 files, ~12.5K LOC)

After Phase 1 import migration, all existing tests should pass without modification (only import
paths change). These tests provide coverage for:

- `intention.py` — `test_riva.py` (1002 lines)
- `executor.py` — `test_code_executor.py`, `test_riva_integration.py`
- `contract.py` — `test_code_contract.py`
- `sandbox.py` — `test_code_sandbox.py`
- `project_memory.py` — `test_project_memory.py`
- All optimization modules — 8 test files

Tests marked `@pytest.mark.slow` require Ollama. Run separately with `pytest -m slow`.

**Test run command (post-migration):**
```bash
cd /home/kellogg/dev/RIVA
PYTHONPATH="src" .venv/bin/python -m pytest tests/ -x --tb=short -q --no-cov
```

### New Tests for Phase 2

```
tests/test_session.py          # create_session(), run_intent() with mocked LLM
tests/test_settings.py         # RIVASettings serialization/deserialization
tests/test_db_schema.py        # Schema migration, all tables present, WAL mode
tests/test_console_observer.py # ConsoleObserver doesn't crash on each callback
tests/test_project.py          # Project dataclass replaces Act correctly
```

### New Tests for Phase 3-5 (TUI)

Textual's test harness (`textual.testing.Pilot`) supports headless TUI testing:

```
tests/tui/
  test_session_screen.py    # Submit intent, receive ExecutionFinished message
  test_diff_screen.py       # Apply/skip individual files modifies sandbox
  test_contract_screen.py   # ContractBuilt message populates widget correctly
  test_memory_screen.py     # Search, edit, delete project memory entries
  test_settings_screen.py   # Settings save/load round-trip
  test_observer.py          # TextualObserver posts correct messages
  test_worker.py            # RIVAWorker completes or cancels cleanly
```

**Key testing pattern for TUI:**
```python
async def test_submit_intent(tmp_path: Path) -> None:
    app = RIVAApp(repo_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.click("#intent-input")
        await pilot.type("add a hello function")
        await pilot.press("enter")
        await pilot.pause(0.1)
        # Check that phase bar moved to INTENT
        phase_bar = app.query_one(PhaseProgressBar)
        assert phase_bar.current_phase == 1
```

### Regression Guard

Add a CI-style test that imports every public symbol from `riva.__init__` to catch broken
re-exports:

```python
# tests/test_public_api.py
def test_all_public_symbols_importable():
    import riva
    for name in riva.__all__:
        assert hasattr(riva, name), f"Missing public symbol: {name}"
```

---

## Files Affected

### Files to Create

```
/home/kellogg/dev/RIVA/
  pyproject.toml                          (NEW — package definition)
  .venv/                                  (NEW — virtual environment)
  src/
    riva/
      __init__.py                         (NEW — public API)
      __main__.py                         (NEW — CLI entry point)
      project.py                          (NEW — replaces Act)
      session.py                          (NEW — session factory and runner)
      settings.py                         (NEW — RIVASettings)
      db_schema.py                        (NEW — SQLite DDL + migrations)
      _vendor/                            (NEW — interim, removed when talkingrock-core published)
        __init__.py
        config.py
        security.py
        errors.py
        db.py
        providers/
          __init__.py
          base.py
          ollama.py
          factory.py
      tui/
        __init__.py                       (NEW)
        app.py                            (NEW — RIVAApp)
        riva.tcss                         (NEW — CSS theme)
        messages.py                       (NEW — Textual Message types)
        observer.py                       (NEW — TextualObserver)
        worker.py                         (NEW — RIVAWorker)
        screens/
          __init__.py                     (NEW)
          session.py                      (NEW — SessionScreen)
          diff.py                         (NEW — DiffScreen)
          contract.py                     (NEW — ContractScreen)
          memory.py                       (NEW — ProjectMemoryScreen)
          settings.py                     (NEW — SettingsScreen)
          history.py                      (NEW — SessionHistoryScreen)
        widgets/
          __init__.py                     (NEW)
          phase_bar.py                    (NEW)
          intent_input.py                 (NEW)
          contract_panel.py               (NEW)
          step_list.py                    (NEW)
          output_log.py                   (NEW)
          diff_view.py                    (NEW)
  tests/
    conftest.py                           (NEW or adapted from existing)
    test_session.py                       (NEW)
    test_settings.py                      (NEW)
    test_db_schema.py                     (NEW)
    test_console_observer.py              (NEW)
    test_project.py                       (NEW)
    test_public_api.py                    (NEW)
    tui/
      __init__.py                         (NEW)
      test_session_screen.py              (NEW)
      test_diff_screen.py                 (NEW)
      test_contract_screen.py             (NEW)
      test_memory_screen.py               (NEW)
      test_settings_screen.py             (NEW)
      test_observer.py                    (NEW)
      test_worker.py                      (NEW)
```

### Files to Move and Rename

```
RIVA/src/code_mode/*.py    →    RIVA/src/riva/*.py
RIVA/src/code_mode/optimization/*.py    →    RIVA/src/riva/optimization/*.py
RIVA/src/code_mode/optimization/parsers/    →    RIVA/src/riva/optimization/parsers/
RIVA/tests/*.py    →    RIVA/tests/*.py    (same location, import paths updated)
```

### Files to Delete

```
RIVA/src/codeModeView.ts           (VS Code only, no TUI equivalent needed)
RIVA/src/diffPreviewOverlay.ts     (replaced by riva/tui/screens/diff.py)
RIVA/src/test_*.py                 (test files at src/ level — they move to tests/)
```

Wait: inspecting the archive, `RIVA/tests/` already exists separately from `RIVA/src/`. The
`test_*.py` files in the top-level `RIVA/` directory (shown in the ls output) are actually
in `RIVA/tests/` — they do not need to move. The `src/` directory only contains `code_mode/` and
the two TypeScript files.

### Files to Modify (import path substitution only)

All 45 files in `src/code_mode/` and all 35 files in `tests/` — mechanical import substitution
only, no logic changes.

---

## Risks and Mitigations

### Risk 1: `talkingrock-core` Does Not Exist Yet

**Probability:** High — no `talkingrock-core` package has been published.
**Impact:** Phase 2 blocked until the dependency exists.
**Mitigation:** The `_vendor/` approach (Step 1.4) provides a working interim. RIVA can develop
and ship phases 1-5 using vendored modules. When `talkingrock-core` is published, the `_vendor/`
directory is deleted and imports are updated to `talkingrock.*`. This is one grep-and-replace pass.

### Risk 2: `ExecutionObserver` Threading Model Incompatibility with Textual

**Probability:** Medium — Textual Workers run in a thread, but Textual's message bus requires
`post_message()` from the worker thread (not `call_from_thread()`).
**Impact:** Phase 3 architecture requires careful attention; wrong approach causes deadlocks or
dropped updates.
**Mitigation:** Use `app.post_message()` from within the Textual Worker thread. Textual 0.80+
supports this. All `TextualObserver` callbacks should only call `self._app.post_message()` — no
direct widget manipulation from the observer. The `on_*` message handlers in the App mutate
widget state on the main thread. This is the standard Textual pattern and is safe.

### Risk 3: `Act.repo_path` Uses Cairn-Specific Semantics

**Probability:** Low — the `Act` usage in RIVA is thin (only `repo_path`, `title`,
`artifact_type`).
**Impact:** If `repo_analyzer.py` or `factory.py` uses other `Act` attributes unexpectedly,
the `Project` stub breaks.
**Mitigation:** Before doing the substitution, audit all `act.` attribute access in those three
files. The full list is: `act.repo_path`, `act.title`, `act.artifact_type`. Confirmed via source
inspection. The `Project` dataclass covers all three.

### Risk 4: `nol_bridge.py` and `intent_to_nol.py` Require the `nol` Binary

**Probability:** High — `nol` is a separate binary project (`/home/kellogg/dev/nol/`).
**Impact:** Tests touching NOL bridge will fail if `nol` is not installed.
**Mitigation:** Tests for `nol_bridge.py` (`test_nol_bridge.py`, `test_riva_nol_integration.py`,
`test_intent_to_nol.py`) should be marked `@pytest.mark.slow` or `@pytest.mark.nol_required` with
a skip condition: `pytest.importorskip("subprocess")` won't work — use a fixture that checks
for the `nol` binary in PATH and skips if absent. This is likely already handled in the existing
`tests/conftest.py`.

### Risk 5: `database.migrate()` Expected by Tests

**Probability:** Medium — `test_project_memory.py` calls `db.migrate()` directly, which in
Cairn's `play_db.py` runs Cairn's full migration chain (v12, v13).
**Impact:** RIVA's standalone `Database` class needs its own `migrate()` that runs only RIVA's
schema (project memory tables).
**Mitigation:** Create `db_schema.py` with RIVA-specific migrations. The `Database.migrate()`
method runs these. Tests that create `Database()` directly will work because `migrate()` creates
only the tables RIVA actually uses. No Cairn-specific tables (conversations, messages, acts) are
created or depended upon.

### Risk 6: `conftest.py` References Cairn-Specific Fixtures

**Probability:** High — the existing tests were written inside the Cairn project and likely import
from `reos.*` for fixtures like `temp_db`, `isolated_db_singleton`.
**Impact:** `conftest.py` will fail to import after the package rename.
**Mitigation:** The `conftest.py` in RIVA's `tests/` directory needs to be rewritten to create
RIVA-specific fixtures. The `temp_db` fixture is straightforward: create an in-memory
`riva.db.Database` and call `migrate()`. The `isolated_db_singleton` fixture resets the
singleton; this pattern applies equally to RIVA's `Database`.

### Risk 7: Textual Version Compatibility

**Probability:** Low — Textual has had breaking API changes between minor versions.
**Impact:** Code written for Textual 0.70 may not work on 0.80+ without changes.
**Mitigation:** Pin to a specific minor version range (`textual>=0.80.0,<1.0.0`) in
`pyproject.toml`. Write TUI code only after confirming the installed version. Reference Textual's
changelog before implementing Phase 3.

### Risk 8: Slow Test Suite Explosion

**Probability:** Medium — with 35 existing test files plus ~15 new ones, the suite grows large.
The `@pytest.mark.slow` pattern is already established.
**Impact:** Long CI feedback loops.
**Mitigation:** Keep the existing `slow` marker convention. Add a `nol_required` marker. Ensure
`pytest` default run excludes both. The non-slow, non-nol suite should complete in under 30
seconds.

---

## Definition of Done

### Phase 1
- [ ] `src/riva/` package exists with all 45 source files (renamed from `code_mode`)
- [ ] `pyproject.toml` present and `pip install -e .` succeeds
- [ ] Zero `reos.*` imports remain in any file under `src/` or `tests/`
- [ ] All 35 test files importable without `ModuleNotFoundError`
- [ ] Tests that passed before still pass (excluding `nol`-dependent and Ollama-dependent)
- [ ] `riva` entry point exists: `python -m riva` prints version line
- [ ] `conftest.py` uses RIVA-specific fixtures, no Cairn dependencies

### Phase 2
- [ ] `riva run /path/to/repo "intent string"` executes full 7-phase loop
- [ ] Console output shows each phase transition, step, and criterion
- [ ] Ollama not-running is handled gracefully (error message, not crash)
- [ ] `~/.riva-data/riva.db` created on first run with correct schema
- [ ] Project memory is populated after a session
- [ ] `riva run` on subsequent sessions uses project memory from previous session
- [ ] All existing tests plus new Phase 2 tests pass

### Phase 3
- [ ] `riva tui /path/to/repo` launches without error
- [ ] SessionScreen renders with correct layout
- [ ] Submitting an intent starts execution and shows live updates
- [ ] PhaseProgressBar advances through all 7 phases
- [ ] OutputLog scrolls automatically
- [ ] DiffScreen shows all changed files with correct diffs
- [ ] Apply/skip individual files works (file content changes on disk)
- [ ] ESC from DiffScreen returns to SessionScreen
- [ ] Cancellation (Ctrl+C) stops execution cleanly
- [ ] TUI tests pass in Textual's headless mode

### Phase 4
- [ ] ContractScreen shows intent, assumptions, all criteria with status
- [ ] ProjectMemoryScreen shows decisions, patterns, corrections with search
- [ ] Editing a memory entry persists to `riva.db`
- [ ] SettingsScreen saves and reloads correctly
- [ ] Changed settings take effect on next session (not hot-reload)
- [ ] TUI tests for all three screens pass

### Phase 5
- [ ] SessionHistoryScreen shows all past sessions with outcomes
- [ ] Session detail view shows full execution trace
- [ ] Approval UX works for both individual step approval and batch approval
- [ ] All error edge cases (Ollama down, bad repo path, timeout) show informative UI
- [ ] `riva --help` and subcommand help texts are complete
- [ ] CSS theme applied consistently across all 6 screens

### Overall
- [ ] Zero `reos.*` or `cairn.*` imports anywhere in RIVA source (only `talkingrock.*` or
  `riva._vendor.*`)
- [ ] `pip install -e ".[tui]"` installs all TUI dependencies
- [ ] `pip install -e ".[dev,tui,parsing]"` installs full dev stack
- [ ] Test suite passes: `pytest tests/ -q --no-cov -m "not slow and not nol_required"`
- [ ] `ruff check src/ tests/` reports zero errors
- [ ] The word "ReOS" does not appear in any user-facing string or UI label

---

## Confidence Assessment

**Phase 1 (Package scaffolding + import migration):** High confidence. The import mapping is
deterministic and mechanical. The only genuine risk is the `conftest.py` rewrite and the `nol`
binary dependency, both of which are known and bounded.

**Phase 2 (CLI entry point):** High confidence. `CodeExecutor`, `ExecutionObserver`, and
`ProjectMemoryStore` are fully implemented and tested. Wiring them together with a `ConsoleObserver`
is straightforward.

**Phase 3 (TUI Session + Diff):** Medium confidence. The `ExecutionObserver` → Textual message
bridge is architecturally sound but requires careful attention to Textual's threading model.
The `streaming.py` module was designed for Tauri RPC polling — its `CodeExecutionContext`
threading abstraction must not be reused for the TUI. Starting fresh with Textual Workers and
`post_message()` is the right call but adds design work.

**Phase 4-5 (Remaining TUI screens):** Medium confidence. These are data-display screens with
standard CRUD interactions. The main unknown is Textual's modal dialog API and whether inline
editing of memory entries is achievable with acceptable UX in a terminal.

---

## Assumptions That Need Validation

1. **Textual 0.80+** — the plan assumes Textual's current API. Confirm the version before
   Phase 3. Earlier versions had different Worker APIs and modal patterns.

2. **`nol` binary availability** — `nol_bridge.py` and `intent_to_nol.py` are in scope for
   import migration but their tests may need the `nol` binary. Confirm whether `nol` is expected
   to be in PATH during RIVA development or whether those modules are "aspirational" in the
   standalone context.

3. **`talkingrock-core` timeline** — if the ReOS standalone plan (referenced in project memory as
   "in progress") produces `talkingrock-core` before RIVA Phase 2 begins, the `_vendor/` approach
   can be skipped entirely. This is worth synchronizing.

4. **`qwen2.5-coder:7b` as default model** — the codebase uses Ollama but does not specify a
   default model for RIVA. `qwen2.5-coder:7b` is a reasonable default for code tasks at modest
   hardware requirements. The user should confirm the preferred default.

5. **Database encryption** — Cairn uses SQLCipher for `reos.db`. RIVA's standalone `riva.db`
   stores project memory (code patterns, decisions) which is less sensitive than Cairn's personal
   data. The plan uses standard SQLite by default with SQLCipher as an optional extra
   (`pip install riva[db-crypto]`). Confirm whether the user wants encryption on by default.
