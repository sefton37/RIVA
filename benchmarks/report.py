"""Benchmark report generator with model comparison."""

from __future__ import annotations

from benchmarks.db import BenchmarkDB


def print_report(db: BenchmarkDB, run_id: str) -> None:
    """Print a summary report for a benchmark run."""
    runs = db.get_recent_runs(100)
    run = next((r for r in runs if r["run_id"] == run_id), None)
    if run is None:
        print(f"Run {run_id} not found.")
        return

    results = db.get_run_results(run_id)
    model = run.get("model_name", "unknown")
    elapsed = run.get("elapsed_seconds")
    elapsed_str = f"{elapsed:.1f}s" if elapsed else "?"

    print(f"Run: {run['run_id']}  Model: {model}  Time: {elapsed_str}")
    print(f"Level: {run['level']}  "
          f"Result: {run['passed']}P / {run['failed']}F / {run['skipped']}S")

    if results:
        print(f"\n  {'Case':<35s} {'Status':<10s} {'Time':<8s} {'Verdict':<12s}")
        print(f"  {'-'*35} {'-'*10} {'-'*8} {'-'*12}")
        for r in results:
            elapsed = f"{r['elapsed_seconds']:.1f}s" if r["elapsed_seconds"] else "-"
            verdict = r["audit_verdict"] or "-"
            print(f"  {r['case_id']:<35s} {r['status']:<10s} {elapsed:<8s} {verdict:<12s}")


def print_comparison(db: BenchmarkDB) -> None:
    """Print a comparison table across all tested models."""
    model_runs = db.get_all_model_runs()

    if not model_runs:
        print("No model comparison data available. Run --all-models first.")
        return

    print("RIVA Model Comparison")
    print("=" * 80)
    print(f"\n{'Model':<35s} {'Params':>6s} {'Status':>8s} {'Time':>8s} {'P':>3s} {'F':>3s}")
    print(f"{'-'*35} {'-'*6} {'-'*8} {'-'*8} {'-'*3} {'-'*3}")

    for run in sorted(model_runs, key=lambda r: r.get("model_params", "0")):
        model = run["model_name"] or "?"
        params = run.get("model_params", "?")
        status = "PASS" if run["failed"] == 0 else "FAIL"
        elapsed = run.get("elapsed_seconds")
        elapsed_str = f"{elapsed:.0f}s" if elapsed else "?"
        passed = run.get("passed", 0)
        failed = run.get("failed", 0)

        print(f"{model:<35s} {params:>6s} {status:>8s} {elapsed_str:>8s} {passed:>3d} {failed:>3d}")

    print(f"\n{len(model_runs)} models compared")
