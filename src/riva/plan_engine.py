"""Plan Engine: Ollama-powered intent decomposition.

Takes a user request and project context, calls Ollama with a JSON-schema
prompt, returns a structured RivaPlan with numbered steps each carrying
a verifiable acceptance criterion.

The decompose() method is async: it returns a plan_id immediately and
runs Ollama in a background asyncio task. The client polls plan status
until it transitions from 'draft' to 'pending_approval'.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from riva.db import get_connection, transaction
from riva.models import PlanStep, RivaPlan

if TYPE_CHECKING:
    from trcore.providers import LLMProvider

logger = logging.getLogger(__name__)

# System prompt for Ollama plan decomposition
_PLAN_SYSTEM_PROMPT = """\
You are a project planning agent. Given a user request, decompose it into
a structured plan with numbered steps.

IMPORTANT: Each step MUST have a verifiable acceptance_criterion. Criteria
must describe something checkable WITHOUT running code:
- A file that should exist (e.g., "src/service.py exists")
- A function that should be defined (e.g., "handle_request defined in src/service.py")
- A git change (e.g., "src/routes/ has changes in git diff")
- A test file created (e.g., "tests/test_service.py exists")
- Manual verification if no automated check is possible

NEVER use vague criteria like "feature works correctly" or "code is clean".

Respond with ONLY valid JSON in this exact format:
{
  "title": "Short descriptive title for the plan",
  "steps": [
    {
      "step_number": 1,
      "title": "Step title",
      "description": "What to do in this step",
      "acceptance_criterion": "Verifiable condition (file exists, function defined, etc.)",
      "estimated_minutes": 15
    }
  ],
  "risks": ["Risk 1", "Risk 2"],
  "estimated_minutes": 30
}
"""

_REPAIR_PROMPT = """\
The previous response was not valid JSON. Fix it and respond with ONLY
the corrected JSON object. No explanation, no markdown, just the JSON.
"""


class PlanEngine:
    """Decomposes user requests into structured plans via Ollama."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider
        self._tasks: dict[str, asyncio.Task] = {}

    def decompose(
        self,
        project_id: str,
        user_request: str,
        project_context: str = "",
    ) -> str:
        """Start async plan decomposition. Returns plan_id immediately.

        The actual Ollama call runs in a background asyncio task.
        Poll via get_plan() until status is 'pending_approval'.

        Args:
            project_id: The RIVA project this plan belongs to.
            user_request: The user's natural language request.
            project_context: Optional context (Act notes, project description).

        Returns:
            plan_id for polling.
        """
        plan_id = f"plan-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        # Insert draft plan
        with transaction() as conn:
            conn.execute(
                "INSERT INTO riva_plans "
                "(id, project_id, title, user_request, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'draft', ?, ?)",
                (plan_id, project_id, "Generating...", user_request, now, now),
            )

        # Launch background task
        if self._provider is not None:
            task = asyncio.ensure_future(
                self._decompose_background(plan_id, user_request, project_context)
            )
            self._tasks[plan_id] = task

        return plan_id

    async def _decompose_background(
        self,
        plan_id: str,
        user_request: str,
        project_context: str,
    ) -> None:
        """Background task: call Ollama and parse the plan."""
        prompt = user_request
        if project_context:
            prompt = f"Project context:\n{project_context}\n\nRequest:\n{user_request}"

        # Try up to 2 times (original + 1 repair)
        raw_json = None
        for attempt in range(2):
            try:
                if attempt == 0:
                    raw = self._provider.chat_json(
                        system=_PLAN_SYSTEM_PROMPT,
                        user=prompt,
                        temperature=0.4,
                    )
                else:
                    raw = self._provider.chat_json(
                        system=_REPAIR_PROMPT,
                        user=raw,  # Send the broken JSON back for repair
                        temperature=0.0,
                    )

                raw_json = json.loads(raw)
                break  # Success

            except json.JSONDecodeError:
                logger.warning(
                    "Plan engine: malformed JSON (attempt %d) for plan %s",
                    attempt + 1,
                    plan_id,
                )
                continue
            except Exception as exc:
                logger.exception("Plan engine: LLM error for plan %s", plan_id)
                self._save_error(plan_id, str(exc))
                return

        if raw_json is None:
            self._save_error(plan_id, "Failed to parse LLM output after 2 attempts")
            return

        # Parse and save
        try:
            self._save_plan(plan_id, raw_json, json.dumps(raw_json))
        except Exception as exc:
            logger.exception("Plan engine: save error for plan %s", plan_id)
            self._save_error(plan_id, str(exc))

    def _save_plan(
        self, plan_id: str, parsed: dict[str, Any], raw_json_str: str
    ) -> None:
        """Save parsed plan to database."""
        now = datetime.now(timezone.utc).isoformat()
        title = parsed.get("title", "Untitled Plan")
        steps = parsed.get("steps", [])

        with transaction() as conn:
            conn.execute(
                "UPDATE riva_plans SET title=?, decomposition_json=?, "
                "status='pending_approval', updated_at=? WHERE id=?",
                (title, raw_json_str, now, plan_id),
            )

            for step in steps:
                step_id = f"step-{uuid4().hex[:12]}"
                conn.execute(
                    "INSERT INTO riva_plan_steps "
                    "(id, plan_id, step_number, title, description, "
                    "acceptance_criterion, estimated_minutes, status, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        step_id,
                        plan_id,
                        step.get("step_number", 0),
                        step.get("title", ""),
                        step.get("description", ""),
                        step.get("acceptance_criterion", ""),
                        step.get("estimated_minutes"),
                        now,
                        now,
                    ),
                )

        logger.info(
            "Plan %s saved: '%s' with %d steps", plan_id, title, len(steps)
        )

    def _save_error(self, plan_id: str, error: str) -> None:
        """Save error state to plan."""
        now = datetime.now(timezone.utc).isoformat()
        with transaction() as conn:
            conn.execute(
                "UPDATE riva_plans SET title=?, status='failed', "
                "decomposition_json=?, updated_at=? WHERE id=?",
                (
                    "Plan generation failed",
                    json.dumps({"error": error}),
                    now,
                    plan_id,
                ),
            )

    def get_plan(self, plan_id: str) -> RivaPlan | None:
        """Retrieve a plan from the database."""
        conn = get_connection(readonly=True)
        try:
            row = conn.execute(
                "SELECT * FROM riva_plans WHERE id=?", (plan_id,)
            ).fetchone()
            if row is None:
                return None

            steps_rows = conn.execute(
                "SELECT * FROM riva_plan_steps WHERE plan_id=? ORDER BY step_number",
                (plan_id,),
            ).fetchall()

            steps = [
                PlanStep(
                    id=s["id"],
                    plan_id=s["plan_id"],
                    step_number=s["step_number"],
                    title=s["title"],
                    description=s["description"] or "",
                    acceptance_criterion=s["acceptance_criterion"] or "",
                    estimated_minutes=s["estimated_minutes"],
                    status=s["status"],
                    created_at=s["created_at"],
                    updated_at=s["updated_at"],
                )
                for s in steps_rows
            ]

            decomp = row["decomposition_json"]
            risks = []
            est = None
            if decomp:
                try:
                    parsed = json.loads(decomp)
                    risks = parsed.get("risks", [])
                    est = parsed.get("estimated_minutes")
                except json.JSONDecodeError:
                    pass

            return RivaPlan(
                id=row["id"],
                project_id=row["project_id"],
                title=row["title"],
                user_request=row["user_request"],
                steps=steps,
                risks=risks,
                estimated_minutes=est,
                agent_id=row["agent_id"],
                status=row["status"],
                decomposition_json=decomp,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        finally:
            conn.close()

    def list_plans(
        self, project_id: str, status: str | None = None
    ) -> list[RivaPlan]:
        """List plans for a project, optionally filtered by status."""
        conn = get_connection(readonly=True)
        try:
            if status:
                rows = conn.execute(
                    "SELECT id FROM riva_plans WHERE project_id=? AND status=? "
                    "ORDER BY created_at DESC",
                    (project_id, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM riva_plans WHERE project_id=? "
                    "ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()

            return [
                plan
                for row in rows
                if (plan := self.get_plan(row["id"])) is not None
            ]
        finally:
            conn.close()
