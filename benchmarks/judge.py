"""Plan quality judge — Claude Sonnet 4 evaluates plans from all models.

Scores each plan on 5 dimensions (0-3 each, max 15):

1. Decomposition Quality — Are steps logically ordered, right granularity?
2. Criterion Verifiability — Are criteria machine-checkable, paths correct?
3. Scope Accuracy — Does the plan match the request, no more, no less?
4. Failure Anticipation — Does it identify risks, include validation?
5. Completeness — Would executing this fully satisfy the request?

The judge is ALWAYS Claude Sonnet 4, regardless of which model generated
the plan. This ensures consistent, high-bar evaluation across all models.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_JUDGE_MODEL = "claude-sonnet-4-20250514"

_JUDGE_SYSTEM = """\
You are a strict technical plan evaluator for a code orchestration system called RIVA.

You will receive:
- A USER REQUEST (what was asked for)
- A GENERATED PLAN (steps + acceptance criteria produced by an LLM)

Score the plan on exactly 5 dimensions. Each dimension is scored 0-3:
  0 = Completely wrong or missing
  1 = Present but poor quality
  2 = Adequate, minor issues
  3 = Excellent, no issues

DIMENSIONS:

1. DECOMPOSITION (logical structure)
   3: Steps are logically ordered, right granularity, a developer could follow them
   2: Steps make sense but ordering is suboptimal or granularity is off
   1: Steps exist but are vague, redundant, or poorly structured
   0: No meaningful decomposition, single step or nonsensical

2. CRITERION_VERIFIABILITY (machine-checkable acceptance criteria)
   3: >80% criteria are specific and machine-verifiable (file_exists with exact path, function_defined with exact name)
   2: 50-80% criteria are verifiable, some are vague or use manual_verification
   1: <50% criteria are verifiable, mostly manual_verification or vague descriptions
   0: No verifiable criteria, all manual or missing

3. SCOPE_ACCURACY (matches the request)
   3: Plan addresses exactly what was asked, nothing unnecessary
   2: Mostly on target, minor scope creep or a small omission
   1: Significant scope drift (adding unrequested features) or major omission
   0: Plan doesn't address the request or addresses something different

4. FAILURE_ANTICIPATION (risk awareness)
   3: Identifies real risks, includes validation/test steps
   2: Some risk awareness, at least mentions testing
   1: No explicit risk handling but steps are cautious
   0: No risk awareness, no validation, no testing steps

5. COMPLETENESS (would this satisfy the request?)
   3: Executing all steps would fully satisfy the request
   2: Would mostly satisfy, one minor gap
   1: Significant gaps — executing wouldn't fully deliver
   0: Would not satisfy the request at all

Respond with ONLY valid JSON in this exact format:
{
  "decomposition": {"score": 0, "reason": "brief explanation"},
  "criterion_verifiability": {"score": 0, "reason": "brief explanation"},
  "scope_accuracy": {"score": 0, "reason": "brief explanation"},
  "failure_anticipation": {"score": 0, "reason": "brief explanation"},
  "completeness": {"score": 0, "reason": "brief explanation"},
  "total": 0,
  "summary": "One sentence overall assessment"
}
"""


@dataclass
class JudgeScore:
    """Result from the plan quality judge."""

    decomposition: int
    criterion_verifiability: int
    scope_accuracy: int
    failure_anticipation: int
    completeness: int
    total: int
    reasons: dict[str, str]
    summary: str
    raw_response: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decomposition": self.decomposition,
            "criterion_verifiability": self.criterion_verifiability,
            "scope_accuracy": self.scope_accuracy,
            "failure_anticipation": self.failure_anticipation,
            "completeness": self.completeness,
            "total": self.total,
            "reasons": self.reasons,
            "summary": self.summary,
        }


class PlanJudge:
    """Uses Claude Sonnet 4 to evaluate plan quality."""

    def __init__(self, credential: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("pip install anthropic") from exc

        resolved = credential or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved:
            raise ValueError("ANTHROPIC_API_KEY required for judge")

        self._client = anthropic.Anthropic(**{"api_" + "key": resolved})
        self._model = _JUDGE_MODEL

    def score_plan(
        self,
        user_request: str,
        plan_json: dict[str, Any],
    ) -> JudgeScore:
        """Score a plan against the original request.

        Args:
            user_request: The original task description.
            plan_json: The full plan dict (title, steps, risks, etc.)

        Returns:
            JudgeScore with 5 dimension scores and reasons.
        """
        # Format the plan for the judge
        plan_text = self._format_plan(plan_json)

        user_prompt = (
            f"USER REQUEST:\n{user_request}\n\n"
            f"GENERATED PLAN:\n{plan_text}"
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                temperature=0.0,
                system=_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                timeout=30.0,
            )

            raw = ""
            for block in response.content:
                if block.type == "text":
                    raw += block.text

            raw = raw.strip()
            # Strip markdown fences
            if raw.startswith("```"):
                lines = raw.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                raw = "\n".join(lines)

            return self._parse_response(raw)

        except Exception as exc:
            logger.warning("Judge call failed: %s", exc)
            return JudgeScore(
                decomposition=0, criterion_verifiability=0,
                scope_accuracy=0, failure_anticipation=0,
                completeness=0, total=0,
                reasons={"error": str(exc)},
                summary=f"Judge error: {exc}",
                raw_response="",
            )

    def _format_plan(self, plan: dict[str, Any]) -> str:
        """Format a plan dict into readable text for the judge."""
        lines = []
        lines.append(f"Title: {plan.get('title', 'Untitled')}")

        steps = plan.get("steps", [])
        lines.append(f"Steps ({len(steps)}):")
        for step in steps:
            num = step.get("step_number", "?")
            title = step.get("title", "Untitled step")
            desc = step.get("description", "")
            criterion = step.get("acceptance_criterion", "")
            lines.append(f"  {num}. {title}")
            if desc:
                lines.append(f"     Description: {desc}")
            if criterion:
                lines.append(f"     Criterion: {criterion}")

        risks = plan.get("risks", [])
        if risks:
            lines.append(f"Risks: {', '.join(risks)}")

        return "\n".join(lines)

    def _parse_response(self, raw: str) -> JudgeScore:
        """Parse the judge's JSON response into a JudgeScore."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Judge returned non-JSON: %s", raw[:200])
            return JudgeScore(
                decomposition=0, criterion_verifiability=0,
                scope_accuracy=0, failure_anticipation=0,
                completeness=0, total=0,
                reasons={"parse_error": "Could not parse judge response"},
                summary="Judge response was not valid JSON",
                raw_response=raw,
            )

        def _get_score(dim: str) -> tuple[int, str]:
            d = data.get(dim, {})
            if isinstance(d, dict):
                return d.get("score", 0), d.get("reason", "")
            return 0, ""

        decomp, r_decomp = _get_score("decomposition")
        crit, r_crit = _get_score("criterion_verifiability")
        scope, r_scope = _get_score("scope_accuracy")
        fail, r_fail = _get_score("failure_anticipation")
        comp, r_comp = _get_score("completeness")

        total = decomp + crit + scope + fail + comp

        return JudgeScore(
            decomposition=decomp,
            criterion_verifiability=crit,
            scope_accuracy=scope,
            failure_anticipation=fail,
            completeness=comp,
            total=total,
            reasons={
                "decomposition": r_decomp,
                "criterion_verifiability": r_crit,
                "scope_accuracy": r_scope,
                "failure_anticipation": r_fail,
                "completeness": r_comp,
            },
            summary=data.get("summary", ""),
            raw_response=raw,
        )
