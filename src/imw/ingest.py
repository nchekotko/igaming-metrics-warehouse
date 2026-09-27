"""Contract-validated ingestion of the landing zone into DuckDB (schema ``raw``).

* one landing file (entity x arrival month) is processed at most once (``raw._ingestion_log``);
* every line is parsed and validated against its contract;
* valid records go to ``raw.<entity>`` with envelope metadata (``_ingested_at``, ``_source``,
  ``_batch_id``, ``_tz_adjusted``, ``_contract_version``);
* violations go to ``raw.quarantine`` with the raw line and the list of errors.

Duplicates are *not* removed here — raw is an append-only record of what arrived. Deduplication
is a staging concern (see ``dbt/models/staging``), so duplicate rates remain measurable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from imw.contracts import ContractViolation, EntityContract, load_contracts, parse_timestamp

META_COLUMNS = [
    ("_ingested_at", "TIMESTAMP"),
    ("_source", "VARCHAR"),
    ("_contract_version", "VARCHAR"),
    ("_tz_adjusted", "BOOLEAN"),
    ("_batch_id", "VARCHAR"),
    ("_loaded_at", "TIMESTAMP"),
]


@dataclass
class FileResult:
    entity: str
    partition: str
    accepted: int
    rejected: int


def ensure_schema(con: duckdb.DuckDBPyConnection, contracts: dict[str, EntityContract]) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    for c in contracts.values():
        cols = ", ".join(f'"{n}" {t}' for n, t in c.ddl_columns() + META_COLUMNS)
        con.execute(f"CREATE TABLE IF NOT EXISTS raw.{c.name} ({cols})")
    con.execute(
        """CREATE TABLE IF NOT EXISTS raw.quarantine (
            entity VARCHAR, partition VARCHAR, line_no BIGINT, source VARCHAR,
            ingested_at TIMESTAMP, raw_line VARCHAR, errors VARCHAR, _batch_id VARCHAR,
            _loaded_at TIMESTAMP)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS raw._ingestion_log (
            entity VARCHAR, partition VARCHAR, file_name VARCHAR, accepted BIGINT, rejected BIGINT,
            _batch_id VARCHAR, _loaded_at TIMESTAMP)"""
    )


def _already_loaded(con: duckdb.DuckDBPyConnection, entity: str, partition: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM raw._ingestion_log WHERE entity = ? AND partition = ?", [entity, partition]
    ).fetchone()
    return bool(row and row[0])


def validate_lines(
    contract: EntityContract, lines: list[str], partition: str, batch_id: str, contract_version: str
) -> tuple[list[dict], list[dict]]:
    ok: list[dict] = []
    bad: list[dict] = []
    for line_no, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        source, ingested_at = None, None
        try:
            env = json.loads(line)
            if not isinstance(env, dict) or "payload" not in env:
                raise ContractViolation(["envelope: missing payload"])
            source = env.get("source")
            if not isinstance(source, str):
                raise ContractViolation(["envelope: missing source"])
            ingested_at, _ = parse_timestamp(str(env.get("ingested_at", "")))
            record, tz_adj = contract.validate(env["payload"], source)
        except json.JSONDecodeError as exc:
            bad.append(
                _reject(contract.name, partition, line_no, source, ingested_at, line, [f"json: {exc.msg}"], batch_id)
            )
            continue
        except (ContractViolation, ValueError) as exc:
            errors = exc.errors if isinstance(exc, ContractViolation) else [f"envelope: {exc}"]
            bad.append(_reject(contract.name, partition, line_no, source, ingested_at, line, errors, batch_id))
            continue
        record.update(
            _ingested_at=ingested_at,
            _source=source,
            _contract_version=contract_version,
            _tz_adjusted=tz_adj,
            _batch_id=batch_id,
        )
        ok.append(record)
    return ok, bad


def _reject(
    entity: str,
    partition: str,
    line_no: int,
    source: str | None,
    ingested_at: datetime | None,
    line: str,
    errors: list[str],
    batch_id: str,
) -> dict:
    return {
        "entity": entity,
        "partition": partition,
        "line_no": line_no,
        "source": source,
        "ingested_at": ingested_at,
        "raw_line": line,
        "errors": json.dumps(errors),
        "_batch_id": batch_id,
    }


def ingest_partition(
    con: duckdb.DuckDBPyConnection, landing: Path, partition: str, batch_id: str | None = None
) -> list[FileResult]:
    """Load every entity file of one arrival month (``YYYY-MM``)."""
    version, contracts = load_contracts()
    ensure_schema(con, contracts)
    batch_id = batch_id or f"batch_{partition}"
    loaded_at = datetime.now(UTC).replace(tzinfo=None)
    results: list[FileResult] = []
    for name, contract in contracts.items():
        path = landing / name / f"arrival_month={partition}.jsonl"
        if not path.exists() or _already_loaded(con, name, partition):
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        ok, bad = validate_lines(contract, lines, partition, batch_id, version)
        if ok:
            df = pd.DataFrame(ok)
            df["_loaded_at"] = loaded_at
            cols = [c for c, _ in contract.ddl_columns() + META_COLUMNS]
            df = df[cols]
            con.execute(f"INSERT INTO raw.{name} SELECT * FROM df")
        if bad:
            qdf = pd.DataFrame(bad)
            qdf["_loaded_at"] = loaded_at
            con.execute("INSERT INTO raw.quarantine SELECT * FROM qdf")
        con.execute(
            "INSERT INTO raw._ingestion_log VALUES (?, ?, ?, ?, ?, ?, ?)",
            [name, partition, path.name, len(ok), len(bad), batch_id, loaded_at],
        )
        results.append(FileResult(name, partition, len(ok), len(bad)))
    return results


def available_partitions(landing: Path) -> list[str]:
    parts = {p.stem.split("=", 1)[1] for p in landing.glob("*/arrival_month=*.jsonl")}
    return sorted(parts)
