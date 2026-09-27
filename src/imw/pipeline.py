"""Orchestration: landing -> raw (monthly arrival batches, dbt snapshot after each) -> dbt build."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb

from imw.config import DBT_DIR, db_path
from imw.ingest import available_partitions, ingest_partition


def run_dbt(args: list[str], db: Path | None = None) -> None:
    """Run dbt against the DuckDB warehouse in a subprocess. Raises on failure.

    A subprocess (not dbtRunner in-process) guarantees dbt-duckdb releases its file lock before
    the next ingestion batch or a read-only analysis connection opens the database.
    """
    env = {**os.environ, "IMW_DUCKDB_PATH": str(db or db_path())}
    full = [sys.executable, "-m", "dbt.cli.main", *args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    res = subprocess.run(full, env=env, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"dbt {' '.join(args)} failed with exit code {res.returncode}")


def load_batches(
    landing: Path, db: Path | None = None, snapshot: bool = True, reset: bool = False
) -> list[tuple[str, int, int]]:
    """Ingest arrival-month partitions in order, taking an SCD2 snapshot after each batch.

    Running ``dbt snapshot`` per batch mimics a daily/monthly production schedule: the
    attribution history in ``snapshots.snap_player_attribution`` is built from what was *known*
    at each load, which is exactly what point-in-time attribution needs.
    """
    db = db or db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    if reset and db.exists():
        db.unlink()
    summary: list[tuple[str, int, int]] = []
    for part in available_partitions(landing):
        con = duckdb.connect(str(db))
        try:
            results = ingest_partition(con, landing, part)
        finally:
            con.close()
        acc = sum(r.accepted for r in results)
        rej = sum(r.rejected for r in results)
        summary.append((part, acc, rej))
        print(f"[ingest] arrival_month={part}: accepted={acc} quarantined={rej}")
        if snapshot and results:
            run_dbt(["snapshot", "--quiet"], db)
    return summary
