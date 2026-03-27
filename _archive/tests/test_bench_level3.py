"""Level 3 benchmark: Claude-judged plan quality scoring.

Generates plans with the test model (Ollama or Claude), then scores
each plan using Claude Sonnet 4 as judge on 5 dimensions (max 15).

This tests reasoning quality, not just JSON output.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from tests.bench_helpers import RivaClient, load_cases

pytestmark = pytest.mark.e2e

_CASES = load_cases("level3_hard.yaml")


def _get_judge():
    """Get the PlanJudge (requires ANTHROPIC_API_KEY)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY required for Level 3 judge")
    from benchmarks.judge import PlanJudge
    return PlanJudge(credential=api_key)


class TestPlanQuality:
    """Score plan quality across hard benchmark cases."""

    @pytest.fixture(autouse=True, scope="class")
    def _setup_judge(self):
        self.__class__._judge = _get_judge()

    @pytest.mark.parametrize("case", _CASES, ids=lambda c: c["case_id"])
    def test_plan_quality(self, riva_client: RivaClient, case):
        min_score = case.get("min_quality_score", 0)
        model = os.environ.get("TALKINGROCK_OLLAMA_MODEL", "unknown")

        print(f"\n  [{case['case_id']}] category={case['category']} model={model}")

        # 1. Create project
        project = riva_client.call(
            "riva/projects/create", name=f"bench3-{case['case_id']}"
        )

        # 2. Generate plan
        print(f"  [{case['case_id']}] Generating plan...")
        start = time.monotonic()

        try:
            plan_start = riva_client.call(
                "riva/plan/create",
                project_id=project["id"],
                user_request=case["request"],
            )
        except RuntimeError as exc:
            # Entry guard or other failure
            plan_time = time.monotonic() - start
            print(f"  [{case['case_id']}] Plan creation failed: {exc}")
            score = self._judge.score_plan(
                case["request"],
                {"title": "FAILED", "steps": [], "risks": []},
            )
            self._report(case, score, plan_time, model, plan_failed=True)
            if min_score > 0:
                assert score.total >= min_score, (
                    f"Score {score.total}/15 < min {min_score}. {score.summary}"
                )
            return

        plan_id = plan_start["plan_id"]

        # 3. Poll until plan is ready
        plan = None
        for _ in range(120):
            time.sleep(0.5)
            plan = riva_client.call("riva/plan/get", plan_id=plan_id)
            if plan["status"] != "draft":
                break

        plan_time = time.monotonic() - start

        if plan is None or plan["status"] == "failed":
            print(f"  [{case['case_id']}] Plan failed: {plan['status'] if plan else 'None'}")
            score = self._judge.score_plan(
                case["request"],
                {"title": "FAILED", "steps": [], "risks": []},
            )
            self._report(case, score, plan_time, model, plan_failed=True)
            return

        # 4. Judge the plan
        print(f"  [{case['case_id']}] Plan: {len(plan.get('steps', []))} steps ({plan_time:.1f}s)")
        print(f"  [{case['case_id']}] Judging...")

        score = self._judge.score_plan(case["request"], plan)
        self._report(case, score, plan_time, model)

        # 5. Assert minimum quality
        if min_score > 0:
            assert score.total >= min_score, (
                f"Score {score.total}/15 < min {min_score}. "
                f"D={score.decomposition} C={score.criterion_verifiability} "
                f"S={score.scope_accuracy} F={score.failure_anticipation} "
                f"K={score.completeness}. {score.summary}"
            )

    def _report(
        self,
        case: dict[str, Any],
        score: Any,
        plan_time: float,
        model: str,
        plan_failed: bool = False,
    ) -> None:
        status = "FAILED" if plan_failed else "OK"
        print(
            f"  [{case['case_id']}] {status} | Score: {score.total}/15 "
            f"(D={score.decomposition} C={score.criterion_verifiability} "
            f"S={score.scope_accuracy} F={score.failure_anticipation} "
            f"K={score.completeness})"
        )
        print(f"  [{case['case_id']}] Judge: {score.summary}")
        for dim, reason in score.reasons.items():
            if reason:
                print(f"    {dim}: {reason[:80]}")
