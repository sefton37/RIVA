"""Benchmark results persistence (SQLite).

Stores results from e2e benchmark runs for tracking model performance.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DB = Path.home() / ".talkingrock" / "riva_benchmarks.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    model_name TEXT,
    model_family TEXT,
    model_params TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    elapsed_seconds REAL,
    level TEXT NOT NULL,
    total_cases INTEGER DEFAULT 0,
    passed INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    level TEXT NOT NULL,
    complexity TEXT,
    status TEXT NOT NULL,
    elapsed_seconds REAL,
    plan_steps INTEGER,
    audit_verdict TEXT,
    events_seen TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);
"""


class BenchmarkDB:
    """SQLite store for benchmark results."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._conn()
        conn.executescript(_SCHEMA)
        conn.close()

    def start_run(
        self, run_id: str, level: str, *, model_name: str | None = None
    ) -> None:
        from benchmarks.models import get_model_info
        info = get_model_info(model_name) if model_name else None
        conn = self._conn()
        conn.execute(
            "INSERT INTO benchmark_runs "
            "(run_id, model_name, model_family, model_params, started_at, level) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                model_name,
                info.get("family") if info else None,
                info.get("params") if info else None,
                datetime.now(timezone.utc).isoformat(),
                level,
            ),
        )
        conn.commit()
        conn.close()

    def complete_run(
        self,
        run_id: str,
        total: int,
        passed: int,
        failed: int,
        skipped: int,
        elapsed_seconds: float | None = None,
    ) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE benchmark_runs SET completed_at=?, total_cases=?, "
            "passed=?, failed=?, skipped=?, elapsed_seconds=? WHERE run_id=?",
            (
                datetime.now(timezone.utc).isoformat(),
                total, passed, failed, skipped, elapsed_seconds, run_id,
            ),
        )
        conn.commit()
        conn.close()

    def record_result(
        self,
        run_id: str,
        case_id: str,
        level: str,
        status: str,
        *,
        complexity: str | None = None,
        elapsed_seconds: float | None = None,
        plan_steps: int | None = None,
        audit_verdict: str | None = None,
        events_seen: str | None = None,
        error_message: str | None = None,
    ) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT INTO benchmark_results "
            "(run_id, case_id, level, complexity, status, elapsed_seconds, "
            "plan_steps, audit_verdict, events_seen, error_message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, case_id, level, complexity, status, elapsed_seconds,
             plan_steps, audit_verdict, events_seen, error_message,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()

    def get_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM benchmark_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_run_results(self, run_id: str) -> list[dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM benchmark_results WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_model_runs(self) -> list[dict[str, Any]]:
        """Get the latest run for each model."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM benchmark_runs WHERE model_name IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "ORDER BY model_name, started_at DESC",
        ).fetchall()
        conn.close()

        # Deduplicate: keep latest per model
        seen: dict[str, dict] = {}
        for r in rows:
            model = r["model_name"]
            if model not in seen:
                seen[model] = dict(r)
        return list(seen.values())
