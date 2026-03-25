#!/usr/bin/env python3
"""RIVA benchmark runner with model comparison.

Usage:
    python -m benchmarks.runner --model qwen2.5:7b       # Single model
    python -m benchmarks.runner --all-models              # All 16 models
    python -m benchmarks.runner --all-models --level 2    # Level 2 only, all models
    python -m benchmarks.runner --report                  # Show recent results
    python -m benchmarks.runner --compare                 # Compare all models
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from benchmarks.db import BenchmarkDB
from benchmarks.models import MODEL_MATRIX, OLLAMA_MODELS, get_model_info
from benchmarks.report import print_comparison, print_report


def run_benchmark(
    model_name: str,
    *,
    level: int | None = None,
    case: str | None = None,
    verbose: bool = False,
    ollama_url: str | None = None,
    structured: bool = False,
    db: BenchmarkDB,
) -> tuple[str, int]:
    """Run the benchmark suite for a single model.

    Returns (run_id, exit_code).
    """
    run_id = f"run-{uuid4().hex[:8]}"
    level_str = f"level{level}" if level else "all"
    model_info = get_model_info(model_name) or {}

    # Set the model — Anthropic or Ollama
    is_api = model_info.get("params") == "api"
    if is_api:
        os.environ["RIVA_BENCHMARK_PROVIDER"] = "anthropic"
        os.environ["RIVA_BENCHMARK_MODEL"] = model_name
        os.environ.pop("TALKINGROCK_OLLAMA_MODEL", None)
    else:
        os.environ["TALKINGROCK_OLLAMA_MODEL"] = model_name
        os.environ.pop("RIVA_BENCHMARK_PROVIDER", None)
        os.environ.pop("RIVA_BENCHMARK_MODEL", None)

    # Set remote Ollama URL if provided (e.g., Tatooine 4080)
    if ollama_url:
        os.environ["RIVA_BENCHMARK_OLLAMA_URL"] = ollama_url
    else:
        os.environ.pop("RIVA_BENCHMARK_OLLAMA_URL", None)

    # Set structured pipeline mode
    if structured:
        os.environ["RIVA_BENCHMARK_STRUCTURED"] = "1"
    else:
        os.environ.pop("RIVA_BENCHMARK_STRUCTURED", None)

    pytest_args = [
        "tests/",
        "-m", "e2e",
        "--tb=line",
        "-q",
    ]

    if level == 1:
        pytest_args.extend(["-k", "test_bench_level1"])
    elif level == 2:
        pytest_args.extend(["-k", "test_bench_level2"])
    elif level == 3:
        pytest_args.extend(["-k", "test_bench_level3"])

    if case:
        pytest_args.extend(["-k", case])

    if verbose:
        pytest_args.extend(["-v", "-s"])

    # Record run
    db.start_run(run_id, level_str, model_name=model_name)

    print(f"\n{'='*60}")
    print(f"Model: {model_name} ({model_info.get('params', '?')})")
    print(f"Run:   {run_id}")
    print(f"Level: {level_str}")
    print(f"{'='*60}")

    start = time.monotonic()
    exit_code = pytest.main(pytest_args)
    elapsed = time.monotonic() - start

    # Record completion
    # pytest exit codes: 0=all passed, 1=some failed, 2=interrupted, 5=no tests
    passed = 1 if exit_code == 0 else 0
    failed = 0 if exit_code == 0 else 1
    db.complete_run(run_id, passed + failed, passed, failed, 0, elapsed_seconds=elapsed)

    status = "PASSED" if exit_code == 0 else f"FAILED (exit {exit_code})"
    print(f"\n{model_name}: {status} in {elapsed:.1f}s")

    return run_id, exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="RIVA Benchmark Runner")
    parser.add_argument("--model", type=str, help="Run with a specific model")
    parser.add_argument("--all-models", action="store_true", help="Run all 16 Ollama models")
    parser.add_argument("--include-claude", action="store_true",
                        help="Include Claude API model (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--ollama-url", type=str, default=None,
                        help="Remote Ollama URL (e.g., http://100.89.26.88:11434 for Tatooine)")
    parser.add_argument("--structured", action="store_true",
                        help="Use structured extraction pipeline instead of free-form JSON")
    parser.add_argument("--level", type=int, choices=[1, 2, 3], default=2)
    parser.add_argument("--case", type=str, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--report", action="store_true", help="Show recent results")
    parser.add_argument("--compare", action="store_true", help="Compare all model results")
    args = parser.parse_args()

    db = BenchmarkDB()

    if args.report:
        runs = db.get_recent_runs(10)
        if not runs:
            print("No benchmark runs found.")
            return
        for run in runs:
            print_report(db, run["run_id"])
            print()
        return

    if args.compare:
        print_comparison(db)
        return

    if args.all_models:
        models = list(OLLAMA_MODELS)
        if args.include_claude:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print("ERROR: --include-claude requires ANTHROPIC_API_KEY env var")
                sys.exit(1)
            models.append("claude-sonnet-4-20250514")
    elif args.model:
        models = [args.model]
    else:
        print("Specify --model MODEL or --all-models")
        parser.print_help()
        sys.exit(1)

    print(f"RIVA Benchmark Suite")
    print(f"Models: {len(models)}")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Level: {args.level or 'all'}")

    results: list[tuple[str, str, int, float]] = []
    total_start = time.monotonic()

    for model in models:
        model_start = time.monotonic()
        run_id, exit_code = run_benchmark(
            model,
            level=args.level,
            case=args.case,
            verbose=args.verbose,
            ollama_url=args.ollama_url,
            structured=args.structured,
            db=db,
        )
        elapsed = time.monotonic() - model_start
        results.append((model, run_id, exit_code, elapsed))

    total_elapsed = time.monotonic() - total_start

    # Summary
    print(f"\n{'='*60}")
    print(f"BENCHMARK COMPLETE — {len(models)} models, {total_elapsed:.0f}s total")
    print(f"{'='*60}")
    print(f"\n{'Model':<35s} {'Status':<10s} {'Time':>8s}")
    print(f"{'-'*35} {'-'*10} {'-'*8}")
    for model, run_id, code, elapsed in results:
        status = "PASS" if code == 0 else "FAIL"
        print(f"{model:<35s} {status:<10s} {elapsed:>7.1f}s")

    passed = sum(1 for _, _, c, _ in results if c == 0)
    failed = len(results) - passed
    print(f"\n{passed}/{len(results)} models passed, {failed} failed")


if __name__ == "__main__":
    main()
