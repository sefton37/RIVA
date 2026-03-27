"""Level 2 benchmark: Full pipeline with simulated agent behaviors.

Tests RIVA's ability to correctly detect success, failure, and ambiguity
in agent output. Uses real Ollama for plans, SimCCManager for agent execution.

Each test case specifies a behavior mode that controls what the simulated
agent produces. The test then verifies RIVA's audit verdict matches
expectations — or documents where it doesn't (audit blind spots).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.bench_helpers import RivaClient, load_cases

pytestmark = pytest.mark.e2e

_CASES = load_cases("level2_agent_dispatch.yaml")


def _get_sim_manager(client: RivaClient) -> Any:
    """Reach into the service to get the SimCCManager for behavior injection.

    This is a test-only hack — in production, behaviors don't exist.
    We access it through the module-level _manager in the agents handler.
    """
    # The SimCCManager is set as module-level state in rpc_handlers.agents
    # and rpc_handlers.sessions — they share the same instance
    from riva.rpc_handlers.agents import _manager
    return _manager


class TestAgentBehaviors:
    """Run each benchmark case through the full RIVA pipeline."""

    @pytest.mark.parametrize("case", _CASES, ids=lambda c: c["case_id"])
    def test_dispatch(self, riva_client: RivaClient, case):
        timeout = case.get("timeout_seconds", 120)
        behavior = case.get("behavior", "perfect")
        expected_audit = case.get("expected_audit", "passed")
        category = case.get("category", "unknown")

        print(f"\n  [{case['case_id']}] category={category} behavior={behavior}")

        # 1. Create project
        project = riva_client.call(
            "riva/projects/create", name=f"bench-{case['case_id']}"
        )

        # 2. Create agent
        agent = riva_client.call(
            "riva/agents/create",
            name=f"agent-{case['case_id'][:30]}",
            purpose="benchmark",
        )
        agent_id = agent["agent_id"]
        agent_cwd = Path(agent["cwd"])

        # 3. Inject behavior into SimCCManager
        sim = _get_sim_manager(riva_client)
        if sim and hasattr(sim, 'set_behavior'):
            sim.set_behavior(agent_id, behavior)

        # 4. Create plan via Ollama
        print(f"  [{case['case_id']}] Decomposing plan...")
        start = time.monotonic()
        try:
            plan_start = riva_client.call(
                "riva/plan/create",
                project_id=project["id"],
                user_request=case["request"],
            )
        except RuntimeError as exc:
            # Entry guard blocked the request
            if "Entry guard" in str(exc) and expected_audit == "blocked":
                print(f"  [{case['case_id']}] Correctly blocked by entry guard")
                print(f"  [{case['case_id']}] TOTAL: {time.monotonic() - start:.1f}s | "
                      f"verdict=blocked expected=blocked | MATCH")
                return
            elif "Entry guard" in str(exc):
                pytest.fail(f"Entry guard blocked unexpectedly: {exc}")
            raise
        plan_id = plan_start["plan_id"]

        # Poll until plan is ready
        plan = None
        for _ in range(120):
            time.sleep(0.5)
            plan = riva_client.call("riva/plan/get", plan_id=plan_id)
            if plan["status"] != "draft":
                break

        plan_time = time.monotonic() - start

        if plan is None or plan["status"] != "pending_approval":
            print(f"  [{case['case_id']}] Plan failed: {plan['status'] if plan else 'None'}")
            # For edge cases where vague requests produce failed plans, that's expected
            if expected_audit == "inconclusive":
                return  # Plan quality issue, not agent issue
            pytest.fail(f"Plan decomposition failed: {plan['status'] if plan else 'None'}")

        steps = plan.get("steps", [])
        print(f"  [{case['case_id']}] Plan: {len(steps)} steps ({plan_time:.1f}s)")

        # 5. Approve plan → contract
        approve = riva_client.call(
            "riva/plan/approve", plan_id=plan_id, agent_id=agent_id
        )
        contract_id = approve["id"]

        # 6. Deploy agent
        deploy_start = time.monotonic()
        deploy = riva_client.call(
            "riva/session/deploy",
            contract_id=contract_id,
            agent_id=agent_id,
        )
        assert deploy["status"] == "deployed"

        # 7. Poll for events
        seen_events: set[str] = set()
        since = 0
        while (time.monotonic() - deploy_start) < timeout:
            try:
                poll = riva_client.call(
                    "riva/session/poll", agent_id=agent_id, since=since
                )
                events = poll.get("events", [])
                since = poll.get("next_index", since)
                for e in events:
                    seen_events.add(e.get("type", ""))
                if "done" in seen_events:
                    break
                if not poll.get("busy", False) and not events:
                    break
            except Exception:
                pass
            time.sleep(0.3)

        deploy_time = time.monotonic() - deploy_start
        print(f"  [{case['case_id']}] Agent: {sorted(seen_events)} ({deploy_time:.1f}s)")

        # 8. Verify expected events
        for expected_event in case.get("expected_events", []):
            if expected_event not in seen_events:
                print(f"  [{case['case_id']}] WARNING: expected event '{expected_event}' not seen")

        # 9. Run audit
        try:
            audit = riva_client.call(
                "riva/audit/trigger",
                contract_id=contract_id,
                agent_cwd=str(agent_cwd),
            )
            verdict = audit.get("overall_verdict", "unknown")
            explanation = audit.get("verdict_explanation", "")
            criteria_results = audit.get("criteria_results", [])
        except Exception as exc:
            verdict = "error"
            explanation = str(exc)
            criteria_results = []

        # 10. Report
        print(f"  [{case['case_id']}] Audit: {verdict} — {explanation}")
        for cr in criteria_results:
            crit = cr.get("criterion", {})
            status = cr.get("status", "?")
            evidence = cr.get("evidence", "")
            print(f"    [{status:>12s}] {crit.get('type', '?')}: {evidence[:60]}")

        # 11. Verify audit matches expected outcome
        notes = case.get("notes", "")

        if expected_audit == "passed":
            assert verdict in ("passed", "partial"), (
                f"Expected audit passed/partial but got {verdict}. {notes}"
            )
        elif expected_audit == "failed":
            assert verdict in ("failed", "partial"), (
                f"Expected audit failed/partial but got {verdict}. {notes}"
            )
        elif expected_audit == "partial":
            assert verdict in ("partial", "passed", "failed", "inconclusive"), (
                f"Unexpected verdict {verdict} for partial expectation. {notes}"
            )
        elif expected_audit == "inconclusive":
            # Any outcome is acceptable for inconclusive expectations
            pass
        # else: no assertion, just observe

        total_time = time.monotonic() - start
        print(f"  [{case['case_id']}] TOTAL: {total_time:.1f}s | "
              f"verdict={verdict} expected={expected_audit} | "
              f"{'MATCH' if verdict == expected_audit else 'DIVERGED'}")
