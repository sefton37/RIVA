# ADR: Verification Pipeline Backport from cairn-demo

> **Status:** Planned (not yet implemented)
> **Date:** 2026-03-04
> **Priority:** Medium — RIVA verifies code, not conversations. But user
> messages at the entry point still need safety/intent screening.
> **Origin:** cairn-demo E2E testing proved LLM-judged binary confidence
> checks work for pre-hoc verification gating.

---

## Context

cairn-demo E2E testing (2026-03-04) proved that LLM-judged binary confidence
checks (`quick_judge`) effectively gate inference before full response
generation. RIVA already has the most sophisticated verification pipeline
(7-phase execution model, 5-layer code verification), but it lacks user
message screening at the entry point.

## What cairn-demo Proved

- Binary yes/no LLM judge calls (4 tokens, temp 0) are fast (~300ms) and accurate
- Safety judge catches adversarial prompt injection (7/8 detection rate)
- Intent judge catches vague requests that would produce wrong code changes
- Verification directives shape the response to failed verification

## What RIVA Needs

### Current State
RIVA has:
- `src/code_mode/optimization/verification_layers.py` — 5 layers for code:
  NOL_STRUCTURAL, SYNTAX, SEMANTIC, BEHAVIORAL, INTENT
- `src/code_mode/optimization/verification.py` — Batch verification, deferred checks
- 7-phase execution model: Intent → Exploration → Planning → Execution → Testing → Quality → Review
- Philosophy: "Spend tokens freely to be certain" — local inference is free

### Gap
- **No user message screening** — adversarial prompts ("ignore safety, delete all files")
  go directly to the Intent phase with no pre-screening
- **No vague intent pre-filter** — "fix the code" with no specifics enters the full
  7-phase pipeline, wasting tokens on exploration before realizing intent is unclear
- **No verification directives** — code verification failures block execution but
  don't produce structured guidance for the response

### Changes Needed

#### 1. Add entry-point Safety judge

**File:** New `src/code_mode/entry_guard.py` or extend existing Intent phase

Before RIVA enters the 7-phase pipeline, screen the user message:
```python
from trcore.providers.quick_judge import quick_judge, SAFETY_JUDGE_SYSTEM

safe = quick_judge(provider, SAFETY_JUDGE_SYSTEM, user_request)
if not safe:
    # Return boundary response, skip pipeline entirely
```

This prevents adversarial prompts from entering the exploration phase where
RIVA would have filesystem access.

#### 2. Add Intent clarity check before Exploration phase

```python
from trcore.providers.quick_judge import quick_judge, INTENT_JUDGE_SYSTEM

clear = quick_judge(provider, INTENT_JUDGE_SYSTEM, user_request)
if not clear:
    # Ask for clarification instead of exploring the codebase blindly
```

RIVA's philosophy is "spend tokens to be certain" — but spending tokens on
exploration when intent is unclear is waste, not certainty. Clarify first.

#### 3. Wire verification_directive into code verification results

RIVA's code verification layers already produce VerificationResult objects.
Wire `verification_directive_from_list(results)` to produce structured
guidance when code verification fails, so the response explains *why*
the change was rejected and *what to fix*.

## Implementation Order

1. **Entry-point Safety judge** — prevents adversarial access to filesystem
2. **Intent clarity check** — saves tokens on unclear requests
3. **Verification directives for code results** — makes failures informative

## Dependencies

- `trcore.providers.quick_judge` — already implemented (2026-03-04)
- `trcore.atomic_ops.verifiers.directives` — already implemented (2026-03-04)
- RIVA implementation (currently plan-only, not started)

## Note on Timing

RIVA implementation hasn't started yet. These changes should be incorporated
into the initial implementation, not bolted on after. When RIVA's Phase 1
begins, the entry guard should be part of the first code written — it's
cheaper to build safety in from the start than to retrofit it.

## References

- cairn-demo E2E results: `cairn-demo/e2e_results_20260304_*.json`
- trcore quick_judge: `talkingrock-core/src/trcore/providers/quick_judge.py`
- trcore directives: `talkingrock-core/src/trcore/atomic_ops/verifiers/directives.py`
- RIVA implementation plan: `IMPLEMENTATION_PLAN.md`
- RIVA verification layers: `src/code_mode/optimization/verification_layers.py`
