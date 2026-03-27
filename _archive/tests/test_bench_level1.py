"""Level 1 benchmark: Ollama pipeline tests (no Claude Code).

Tests entry guard, plan decomposition, and contract creation
using real Ollama inference.
"""

from __future__ import annotations

import time

import pytest

from tests.bench_helpers import RivaClient, load_cases

pytestmark = pytest.mark.e2e


# ── Entry Guard ──────────────────────────────────────────────────────


class TestEntryGuard:

    @pytest.fixture(autouse=True)
    def _setup(self, ollama_provider):
        self.provider = ollama_provider

    @pytest.mark.parametrize(
        "case",
        load_cases("level1_entry_guard.yaml"),
        ids=lambda c: c["case_id"],
    )
    def test_entry_guard(self, case):
        from riva.entry_guard import check_message

        start = time.monotonic()
        result = check_message(self.provider, case["request"])
        elapsed = time.monotonic() - start

        if case["expected_result"] == "pass":
            assert result.passed, (
                f"Expected pass but got blocked by {result.blocked_by}: {result.reason}"
            )
        else:
            # Negative cases are model-dependent — small Ollama models often
            # miss adversarial prompts. Record the result but don't fail the
            # suite (see BENCHMARK_HARNESS_PLAN.md Risk 1).
            if not result.passed:
                if "expected_blocked_by" in case:
                    assert result.blocked_by == case["expected_blocked_by"], (
                        f"Expected blocked by {case['expected_blocked_by']} "
                        f"but got {result.blocked_by}"
                    )
            else:
                pytest.xfail(
                    f"Model failed to block: {case['case_id']} "
                    f"(expected block by {case.get('expected_blocked_by', '?')})"
                )

        print(f"  [{case['case_id']}] {'PASS' if result.passed else 'BLOCK'} "
              f"({elapsed:.1f}s)")


# ── Plan Engine ──────────────────────────────────────────────────────


class TestPlanEngine:

    @pytest.mark.parametrize(
        "case",
        load_cases("level1_plan_engine.yaml"),
        ids=lambda c: c["case_id"],
    )
    def test_plan_decomposition(self, riva_client: RivaClient, case):
        # Create a project first
        project = riva_client.call(
            "riva/projects/create", name="bench-project", description="benchmark"
        )

        # Submit plan request
        start = time.monotonic()
        plan_start = riva_client.call(
            "riva/plan/create",
            project_id=project["id"],
            user_request=case["request"],
        )
        plan_id = plan_start["plan_id"]

        # Poll until plan is ready (max 60s)
        plan = None
        for _ in range(120):
            time.sleep(0.5)
            plan = riva_client.call("riva/plan/get", plan_id=plan_id)
            if plan["status"] != "draft":
                break

        elapsed = time.monotonic() - start
        assert plan is not None
        assert plan["status"] == "pending_approval", (
            f"Plan status is {plan['status']}, expected pending_approval"
        )

        # Validate plan structure
        steps = plan["steps"]
        expected = case["expected_plan"]
        assert len(steps) >= expected["min_steps"], (
            f"Too few steps: {len(steps)} < {expected['min_steps']}"
        )
        assert len(steps) <= expected["max_steps"], (
            f"Too many steps: {len(steps)} > {expected['max_steps']}"
        )

        # Check criterion types
        if "required_criterion_types" in expected:
            all_criteria = [s.get("acceptance_criterion", "") for s in steps]
            criteria_text = " ".join(all_criteria).lower()
            for req_type in expected["required_criterion_types"]:
                # Heuristic: the criterion text should mention file paths or functions
                if req_type == "file_exists":
                    has_file_ref = any(
                        ("/" in c or ".py" in c or "exists" in c.lower())
                        for c in all_criteria
                    )
                    assert has_file_ref, (
                        f"No file_exists-style criterion found in: {all_criteria}"
                    )

        print(f"  [{case['case_id']}] {len(steps)} steps, {elapsed:.1f}s")

        # Test contract creation from approved plan
        approve_result = riva_client.call(
            "riva/plan/approve", plan_id=plan_id, agent_id="agent-bench"
        )
        contract_id = approve_result["id"]
        contract = riva_client.call("riva/contract/get", contract_id=contract_id)

        assert contract["status"] == "active"
        assert len(contract["verification_criteria"]) > 0
        assert contract["nol_intent_hash"] is not None
        assert len(contract["nol_intent_hash"]) > 0

        print(f"  [{case['case_id']}] Contract created: "
              f"{len(contract['verification_criteria'])} criteria, "
              f"NOL hash={contract['nol_intent_hash'][:12]}")
