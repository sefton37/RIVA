#!/usr/bin/env python3
"""One-time migration: product.db -> talkingrock.db pm_* tables.

Reads all PM data from the standalone product.db, remaps integer IDs
to RIVA-style text IDs (epic-00000000005), and inserts into talkingrock.db.

Usage:
    python scripts/migrate_product_db.py
    python scripts/migrate_product_db.py --source /path/to/product.db --dest /path/to/talkingrock.db
    python scripts/migrate_product_db.py --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Default paths
DEFAULT_SOURCE = Path.home() / "talking-rock" / "product" / "db" / "product.db"
DEFAULT_DEST = Path.home() / ".talkingrock" / "talkingrock.db"


def _remap_id(prefix: str, old_id: int) -> str:
    """Deterministic ID remapping: old integer -> text ID."""
    return f"{prefix}-{str(old_id).zfill(11)}"


def _open_db(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(source_path: Path, dest_path: Path, *, dry_run: bool = False) -> None:
    if not source_path.exists():
        print(f"ERROR: Source database not found: {source_path}")
        sys.exit(1)
    if not dest_path.exists():
        print(f"ERROR: Destination database not found: {dest_path}")
        print("  Start the RIVA service once to create the schema, then re-run.")
        sys.exit(1)

    src = _open_db(source_path, readonly=True)
    dest = _open_db(dest_path)

    # Ensure pm_* tables exist in dest
    # Import schema module to create tables
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from riva.schema import ensure_schema
    ensure_schema(dest)

    # Build ID remapping dicts
    epic_map: dict[int, str] = {}
    cycle_map: dict[int, str] = {}
    issue_map: dict[int, str] = {}
    roadmap_map: dict[int, str] = {}
    research_map: dict[int, str] = {}

    # Read source data
    epics = src.execute("SELECT * FROM epics").fetchall()
    cycles = src.execute("SELECT * FROM cycles").fetchall()
    issues = src.execute("SELECT * FROM issues").fetchall()
    roadmap = src.execute("SELECT * FROM roadmap").fetchall()
    research = src.execute("SELECT * FROM research").fetchall()
    cycle_issues = src.execute("SELECT * FROM cycle_issues").fetchall()
    roadmap_epics = src.execute("SELECT * FROM roadmap_epics").fetchall()

    for row in epics:
        epic_map[row["id"]] = _remap_id("epic", row["id"])
    for row in cycles:
        cycle_map[row["id"]] = _remap_id("cycle", row["id"])
    for row in issues:
        issue_map[row["id"]] = _remap_id("issue", row["id"])
    for row in roadmap:
        roadmap_map[row["id"]] = _remap_id("road", row["id"])
    for row in research:
        research_map[row["id"]] = _remap_id("res", row["id"])

    print(f"Source: {source_path}")
    print(f"Dest:   {dest_path}")
    print(f"  Epics:    {len(epics)}")
    print(f"  Cycles:   {len(cycles)}")
    print(f"  Issues:   {len(issues)}")
    print(f"  Roadmap:  {len(roadmap)}")
    print(f"  Research: {len(research)}")
    print(f"  Cycle-Issues: {len(cycle_issues)}")
    print(f"  Roadmap-Epics: {len(roadmap_epics)}")
    print()

    if dry_run:
        print("DRY RUN — no data written")
        print()
        print("ID Mapping (epics):")
        for old, new in epic_map.items():
            name = next(r["name"] for r in epics if r["id"] == old)
            print(f"  {old} -> {new}  ({name})")
        src.close()
        dest.close()
        return

    # Insert in FK-dependency order, all in one transaction
    cursor = dest.cursor()
    try:
        cursor.execute("BEGIN")

        # 1. Epics (no deps)
        for row in epics:
            new_id = epic_map[row["id"]]
            cursor.execute(
                "INSERT OR IGNORE INTO pm_epics "
                "(id, name, status, project, priority, target_quarter, owner, "
                "description, success_criteria, notes, act_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (new_id, row["name"], row["status"], row["project"],
                 row["priority"], row["target_quarter"], row["owner"],
                 row["description"], row["success_criteria"], row["notes"],
                 row["created_at"], row["updated_at"]),
            )

        # 2. Cycles (no deps)
        for row in cycles:
            new_id = cycle_map[row["id"]]
            cursor.execute(
                "INSERT OR IGNORE INTO pm_cycles "
                "(id, name, status, start_date, end_date, goal, retrospective, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, row["name"], row["status"], row["start_date"],
                 row["end_date"], row["goal"], row["retrospective"],
                 row["created_at"], row["updated_at"]),
            )

        # 3. Roadmap (no deps)
        for row in roadmap:
            new_id = roadmap_map[row["id"]]
            cursor.execute(
                "INSERT OR IGNORE INTO pm_roadmap "
                "(id, name, status, quarter, project, description, why, "
                "dependencies, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, row["name"], row["status"], row["quarter"],
                 row["project"], row["description"], row["why"],
                 row["dependencies"], row["created_at"], row["updated_at"]),
            )

        # 4. Issues (depends on epics, cycles)
        for row in issues:
            new_id = issue_map[row["id"]]
            new_epic_id = epic_map.get(row["epic_id"]) if row["epic_id"] else None
            new_cycle_id = cycle_map.get(row["cycle_id"]) if row["cycle_id"] else None
            cursor.execute(
                "INSERT OR IGNORE INTO pm_issues "
                "(id, name, status, priority, type, epic_id, cycle_id, estimate, "
                "assignee, forgejo_link, branch, acceptance_criteria, notes, "
                "riva_contract_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (new_id, row["name"], row["status"], row["priority"],
                 row["type"], new_epic_id, new_cycle_id, row["estimate"],
                 row["assignee"], row["forgejo_link"], row["branch"],
                 row["acceptance_criteria"], row["notes"],
                 row["created_at"], row["updated_at"]),
            )

        # 5. Cycle-Issues (depends on cycles, issues)
        for row in cycle_issues:
            new_cycle = cycle_map.get(row["cycle_id"])
            new_issue = issue_map.get(row["issue_id"])
            if new_cycle and new_issue:
                cursor.execute(
                    "INSERT OR IGNORE INTO pm_cycle_issues (cycle_id, issue_id) "
                    "VALUES (?, ?)",
                    (new_cycle, new_issue),
                )

        # 6. Roadmap-Epics (depends on roadmap, epics)
        for row in roadmap_epics:
            new_road = roadmap_map.get(row["roadmap_id"])
            new_epic = epic_map.get(row["epic_id"])
            if new_road and new_epic:
                cursor.execute(
                    "INSERT OR IGNORE INTO pm_roadmap_epics (roadmap_id, epic_id) "
                    "VALUES (?, ?)",
                    (new_road, new_epic),
                )

        # 7. Research (depends on epics, issues)
        for row in research:
            new_id = research_map[row["id"]]
            new_epic_id = epic_map.get(row["epic_id"]) if row["epic_id"] else None
            new_issue_id = issue_map.get(row["issue_id"]) if row["issue_id"] else None
            cursor.execute(
                "INSERT OR IGNORE INTO pm_research "
                "(id, name, type, status, project, epic_id, issue_id, source, "
                "key_finding, date, tags, doc_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, row["name"], row["type"], row["status"],
                 row["project"], new_epic_id, new_issue_id, row["source"],
                 row["key_finding"], row["date"], row["tags"], row["doc_path"],
                 row["created_at"], row["updated_at"]),
            )

        dest.commit()
        print("Migration committed successfully.")

    except Exception:
        dest.rollback()
        print("ERROR: Migration rolled back.")
        raise
    finally:
        cursor.close()

    # Verify row counts
    print()
    print("Verification:")
    tables = [
        ("epics", "pm_epics"),
        ("cycles", "pm_cycles"),
        ("issues", "pm_issues"),
        ("roadmap", "pm_roadmap"),
        ("research", "pm_research"),
        ("cycle_issues", "pm_cycle_issues"),
        ("roadmap_epics", "pm_roadmap_epics"),
    ]
    all_ok = True
    for src_table, dst_table in tables:
        src_count = src.execute(f"SELECT COUNT(*) FROM {src_table}").fetchone()[0]
        dst_count = dest.execute(f"SELECT COUNT(*) FROM {dst_table}").fetchone()[0]
        status = "OK" if dst_count >= src_count else "MISMATCH"
        if status == "MISMATCH":
            all_ok = False
        print(f"  {src_table:20s} -> {dst_table:20s}  {src_count:3d} -> {dst_count:3d}  [{status}]")

    print()
    if all_ok:
        print("All row counts match. Migration complete.")
    else:
        print("WARNING: Some row counts do not match. Investigate above.")

    # Print epic ID mapping for reference
    print()
    print("Epic ID Mapping:")
    for old, new in sorted(epic_map.items()):
        name = next(r["name"] for r in epics if r["id"] == old)
        print(f"  {old:3d} -> {new}  ({name})")

    src.close()
    dest.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate product.db to talkingrock.db pm_* tables")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Source product.db path")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="Destination talkingrock.db path")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be migrated without writing")
    args = parser.parse_args()

    migrate(args.source, args.dest, dry_run=args.dry_run)
