"""Simulated CCManager for safe e2e testing.

Drop-in replacement for trcore.CCManager that produces configurable
agent behavior WITHOUT spawning any subprocess.

Behavior modes control what the simulated agent does:

    perfect        — Writes all expected files with correct content, commits
    partial        — Writes some files, skips others
    wrong_name     — Writes files with slightly wrong names (hello_.py vs hello.py)
    wrong_function — Writes file but with wrong function names
    empty_file     — Creates files but they're empty
    extra_files    — Writes expected files PLUS unexpected extras
    no_commit      — Writes files but doesn't git commit
    stub_only      — Creates files with 'pass' implementations only
    crash          — Emits error event, writes nothing
    slow_partial   — Writes first file, then "times out" (no done event)

Behaviors are set per-agent via set_behavior(agent_id, behavior).
Default behavior is "perfect" (parses prompt and writes correct output).
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT = Path.home() / "dev" / "talkingrock" / "agents"

# All valid behavior modes
BEHAVIORS = {
    "perfect", "partial", "wrong_name", "wrong_function", "empty_file",
    "extra_files", "no_commit", "stub_only", "crash", "slow_partial",
}


@dataclass
class SimAgentProcess:
    agent_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    busy: bool = False


class SimCCManager:
    """Simulated CCManager with configurable agent behavior."""

    def __init__(self, db) -> None:
        self._db = db
        self._procs: dict[str, SimAgentProcess] = {}
        self._behaviors: dict[str, str] = {}  # agent_id -> behavior mode
        self._on_session_complete = None

    def set_behavior(self, agent_id: str, behavior: str) -> None:
        """Set the behavior mode for an agent's next session."""
        if behavior not in BEHAVIORS:
            raise ValueError(f"Unknown behavior: {behavior}. Valid: {sorted(BEHAVIORS)}")
        self._behaviors[agent_id] = behavior

    def on_session_complete(self, callback) -> None:
        self._on_session_complete = callback

    # ── Agent CRUD ──

    def list_agents(self, username: str) -> list[dict[str, Any]]:
        conn = self._db.get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, slug, purpose, cwd FROM cc_agents WHERE username=?",
                (username,),
            ).fetchall()
            result = []
            for r in rows:
                proc = self._procs.get(r["id"])
                result.append({
                    "id": r["id"],
                    "name": r["name"],
                    "slug": r["slug"],
                    "purpose": r["purpose"] or "",
                    "cwd": r["cwd"],
                    "busy": proc.busy if proc else False,
                })
            return result
        finally:
            conn.close()

    def create_agent(
        self, username: str, name: str, *, purpose: str = ""
    ) -> dict[str, Any]:
        agent_id = f"agent-{uuid4().hex[:12]}"
        slug = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")[:40]
        cwd = str(_WORKSPACE_ROOT / slug)
        now = datetime.now(timezone.utc).isoformat()

        workspace = Path(cwd)
        workspace.mkdir(parents=True, exist_ok=True)

        subprocess.run(["git", "init", str(workspace)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(workspace), "config", "user.email", "riva-sim@talkingrock"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "config", "user.name", "RIVA Sim"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
        )

        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO cc_agents (id, username, name, slug, purpose, cwd, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (agent_id, username, name, slug, purpose, cwd, now, now),
            )

        return {
            "id": agent_id,
            "agent_id": agent_id,
            "name": name,
            "slug": slug,
            "cwd": cwd,
        }

    def delete_agent(self, agent_id: str) -> dict[str, Any]:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM cc_agents WHERE id=?", (agent_id,))
        self._procs.pop(agent_id, None)
        self._behaviors.pop(agent_id, None)
        return {"deleted": True}

    # ── Send Message ──

    async def send_message(self, agent_id: str, text: str) -> dict[str, Any]:
        conn = self._db.get_connection()
        try:
            row = conn.execute(
                "SELECT cwd FROM cc_agents WHERE id=?", (agent_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Agent not found: {agent_id}")
            cwd = Path(row["cwd"])
        finally:
            conn.close()

        proc = SimAgentProcess(agent_id=agent_id, busy=True)
        self._procs[agent_id] = proc

        now = datetime.now(timezone.utc).isoformat()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO cc_history (agent_id, role, content, created_at) "
                "VALUES (?, 'user', ?, ?)",
                (agent_id, text, now),
            )

        behavior = self._behaviors.get(agent_id, "perfect")
        files = self._parse_expected_files(text)

        await self._run_behavior(proc, cwd, text, files, behavior)

        return {"agent_id": agent_id, "status": "accepted"}

    # ── File Parsing ──

    def _parse_expected_files(self, prompt: str) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        seen: set[str] = set()

        for match in re.finditer(r"\[file_exists\]\s*(?:path:\s*)?(\S+\.py)", prompt):
            path = match.group(1)
            if path not in seen:
                files.append({"path": path, "functions": []})
                seen.add(path)

        for match in re.finditer(
            r"\[function_defined\]\s*(?:file:\s*)?(\S+\.py)\s*(?:name:\s*)?(\w+)", prompt
        ):
            fp, fn = match.group(1), match.group(2)
            existing = next((f for f in files if f["path"] == fp), None)
            if existing:
                existing["functions"].append(fn)
            else:
                files.append({"path": fp, "functions": [fn]})
                seen.add(fp)

        for match in re.finditer(
            r"(?:create|write)\s+(?:a\s+)?(?:file\s+)?(?:named?\s+)?(\w+\.py)",
            prompt, re.IGNORECASE,
        ):
            path = match.group(1)
            if path not in seen:
                files.append({"path": path, "functions": []})
                seen.add(path)

        for match in re.finditer(
            r"(?:function|def)\s+(\w+).*?(?:in\s+)?(\w+\.py)", prompt, re.IGNORECASE
        ):
            fn, fp = match.group(1), match.group(2)
            existing = next((f for f in files if f["path"] == fp), None)
            if existing:
                if fn not in existing["functions"]:
                    existing["functions"].append(fn)
            elif fp not in seen:
                files.append({"path": fp, "functions": [fn]})
                seen.add(fp)

        if not files:
            files.append({"path": "output.py", "functions": []})

        return files

    # ── Behavior Execution ──

    async def _run_behavior(
        self,
        proc: SimAgentProcess,
        cwd: Path,
        prompt: str,
        files: list[dict[str, Any]],
        behavior: str,
    ) -> None:
        """Execute the specified behavior."""
        if behavior == "crash":
            await self._behave_crash(proc)
        elif behavior == "slow_partial":
            await self._behave_slow_partial(proc, cwd, files)
        else:
            # All other behaviors write files (with variations)
            await self._behave_write(proc, cwd, prompt, files, behavior)

    async def _behave_crash(self, proc: SimAgentProcess) -> None:
        """Agent crashes with an error."""
        proc.events.append({
            "type": "assistant_delta",
            "text": "I'll start working on this...\n",
        })
        await asyncio.sleep(0.02)
        proc.events.append({
            "type": "error",
            "text": "Error: Process terminated unexpectedly (simulated crash)",
        })
        proc.events.append({"type": "done"})
        proc.busy = False
        self._record_response(proc.agent_id, "Agent crashed during execution.")

    async def _behave_slow_partial(
        self, proc: SimAgentProcess, cwd: Path, files: list[dict[str, Any]]
    ) -> None:
        """Agent writes first file then hangs — no done event."""
        proc.events.append({
            "type": "assistant_delta",
            "text": "Starting work...\n",
        })
        await asyncio.sleep(0.02)

        if files:
            f = files[0]
            full_path = cwd / f["path"]
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(self._gen_content(f["path"], f["functions"], "perfect"))

            proc.events.append({"type": "tool_use", "tool": "Write", "input": f"Writing {f['path']}"})
            proc.events.append({"type": "tool_result", "text": f"Created {f['path']}", "is_error": False})
            proc.events.append({
                "type": "assistant_delta",
                "text": "Working on the next file...\n",
            })

        # No done event — simulates a hang/timeout
        proc.busy = False  # But mark not busy so polling doesn't loop forever

    async def _behave_write(
        self,
        proc: SimAgentProcess,
        cwd: Path,
        prompt: str,
        files: list[dict[str, Any]],
        behavior: str,
    ) -> None:
        """Write files with behavior variations."""
        proc.events.append({
            "type": "assistant_delta",
            "text": f"I'll work on this task. Creating {len(files)} file(s).\n",
        })
        await asyncio.sleep(0.02)

        files_to_write = self._apply_behavior_to_files(files, behavior)

        for file_info in files_to_write:
            path = file_info["path"]
            content = file_info["content"]
            full_path = cwd / path

            proc.events.append({"type": "tool_use", "tool": "Write", "input": f"Writing {path}"})
            await asyncio.sleep(0.01)

            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

            proc.events.append({"type": "tool_result", "text": f"Created: {path}", "is_error": False})
            proc.events.append({
                "type": "assistant_delta",
                "text": f"Wrote {path}\n",
            })

        # Git commit (unless no_commit behavior)
        if behavior != "no_commit":
            try:
                subprocess.run(["git", "-C", str(cwd), "add", "-A"], capture_output=True, timeout=5)
                subprocess.run(
                    ["git", "-C", str(cwd), "commit", "-m", "feat: simulated agent output"],
                    capture_output=True, timeout=5,
                )
                proc.events.append({"type": "tool_use", "tool": "Bash", "input": "git commit"})
                proc.events.append({"type": "tool_result", "text": "Committed", "is_error": False})
            except Exception:
                pass

        self._record_response(proc.agent_id, f"Created {len(files_to_write)} file(s).")
        proc.events.append({"type": "done"})
        proc.busy = False

        logger.info("Sim agent %s: behavior=%s, files=%d", proc.agent_id, behavior, len(files_to_write))

    def _apply_behavior_to_files(
        self, files: list[dict[str, Any]], behavior: str
    ) -> list[dict[str, str]]:
        """Transform file list based on behavior mode."""
        result: list[dict[str, str]] = []

        if behavior == "perfect":
            for f in files:
                result.append({
                    "path": f["path"],
                    "content": self._gen_content(f["path"], f["functions"], "perfect"),
                })

        elif behavior == "partial":
            # Write only the first file, skip the rest
            if files:
                f = files[0]
                result.append({
                    "path": f["path"],
                    "content": self._gen_content(f["path"], f["functions"], "perfect"),
                })

        elif behavior == "wrong_name":
            # Write files with mangled names (prefix with underscore)
            for f in files:
                name = f["path"]
                stem = Path(name).stem
                wrong_name = f"_{stem}.py"
                result.append({
                    "path": wrong_name,
                    "content": self._gen_content(f["path"], f["functions"], "perfect"),
                })

        elif behavior == "wrong_function":
            # Write correct files but with wrong function names
            for f in files:
                wrong_funcs = [fn + "_impl" for fn in f["functions"]] if f["functions"] else []
                result.append({
                    "path": f["path"],
                    "content": self._gen_content(f["path"], wrong_funcs, "perfect"),
                })

        elif behavior == "empty_file":
            # Create files but they're empty
            for f in files:
                result.append({"path": f["path"], "content": ""})

        elif behavior == "extra_files":
            # Write expected files plus extras
            for f in files:
                result.append({
                    "path": f["path"],
                    "content": self._gen_content(f["path"], f["functions"], "perfect"),
                })
            result.append({
                "path": "debug_notes.py",
                "content": "# This file shouldn't be here\nimport os\nos.system('echo leaked')\n",
            })
            result.append({
                "path": ".env.backup",
                "content": "SECRET=oops_this_shouldnt_exist\n",
            })

        elif behavior == "stub_only":
            # Create files with 'pass' implementations
            for f in files:
                result.append({
                    "path": f["path"],
                    "content": self._gen_content(f["path"], f["functions"], "stub"),
                })

        elif behavior == "no_commit":
            # Write correct files but skip git commit (handled in caller)
            for f in files:
                result.append({
                    "path": f["path"],
                    "content": self._gen_content(f["path"], f["functions"], "perfect"),
                })

        return result

    def _gen_content(
        self, path: str, functions: list[str], quality: str
    ) -> str:
        """Generate file content."""
        lines = [f'"""Generated by RIVA simulated agent."""\n']

        if functions:
            for func in functions:
                if quality == "stub":
                    lines.append(f"def {func}(*args, **kwargs):")
                    lines.append("    pass  # TODO: implement")
                elif "add" in func.lower():
                    lines.append(f"def {func}(a, b):")
                    lines.append("    return a + b")
                elif "subtract" in func.lower():
                    lines.append(f"def {func}(a, b):")
                    lines.append("    return a - b")
                elif "multiply" in func.lower():
                    lines.append(f"def {func}(a, b):")
                    lines.append("    return a * b")
                elif "divide" in func.lower():
                    lines.append(f"def {func}(a, b):")
                    lines.append("    if b == 0:")
                    lines.append("        raise ValueError('Division by zero')")
                    lines.append("    return a / b")
                else:
                    if quality == "stub":
                        lines.append(f"def {func}(*args, **kwargs):")
                        lines.append("    pass")
                    else:
                        lines.append(f"def {func}(*args, **kwargs):")
                        lines.append(f'    """Implement {func}."""')
                        lines.append("    return None")
                lines.append("")
        elif "test" in path.lower():
            lines.append("import pytest\n")
            lines.append("def test_basic():")
            lines.append("    assert True\n")
        elif "hello" in path.lower():
            lines.append('print("hello world")\n')
        else:
            lines.append("# Module placeholder")
            lines.append("pass\n")

        return "\n".join(lines) + "\n"

    def _record_response(self, agent_id: str, text: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO cc_history (agent_id, role, content, created_at) "
                "VALUES (?, 'assistant', ?, ?)",
                (agent_id, text, now),
            )

    # ── Poll / Stop / History ──

    def poll_events(self, agent_id: str, since: int = 0) -> dict[str, Any]:
        proc = self._procs.get(agent_id)
        if proc is None:
            return {"events": [], "next_index": 0, "busy": False}
        events = proc.events[since:]
        return {"events": events, "next_index": len(proc.events), "busy": proc.busy}

    async def stop_session(self, agent_id: str) -> dict[str, Any]:
        proc = self._procs.get(agent_id)
        if proc is not None:
            proc.busy = False
            proc.events.append({"type": "done"})
        return {"stopped": True}

    def get_history(self, agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._db.get_connection()
        try:
            rows = conn.execute(
                "SELECT role, content, created_at FROM cc_history "
                "WHERE agent_id=? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
