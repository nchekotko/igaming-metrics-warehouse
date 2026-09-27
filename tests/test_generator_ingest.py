import hashlib
import json
import random
from pathlib import Path

import duckdb
import pytest

from imw.config import GeneratorConfig
from imw.generator import assign_arm, generate, win_multiplier
from imw.ingest import available_partitions, ingest_partition, validate_lines


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(root.glob("*/arrival_month=*.jsonl")):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def landing(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("landing")
    generate(root, GeneratorConfig(seed=7, n_players=60, invalid_rate=0.02, duplicate_rate=0.02))
    return root


def test_generator_is_deterministic(landing, tmp_path):
    generate(tmp_path, GeneratorConfig(seed=7, n_players=60, invalid_rate=0.02, duplicate_rate=0.02))
    assert _digest(tmp_path) == _digest(landing)


def test_manifest_labels_synthetic(landing):
    m = json.loads((landing / "_manifest.json").read_text(encoding="utf-8"))
    assert "SYNTHETIC" in m["label"]
    assert m["injected"]["wallet_transactions"]["events"] > 0


def test_rtp_close_to_target():
    r = random.Random(1)
    n = 1_000_000
    rtp = sum(win_multiplier(r) for _ in range(n)) / n
    assert 0.90 < rtp < 1.01  # target ~0.956; the tail makes the sample mean noisy


def test_arm_assignment_balanced_and_stable():
    arms = [assign_arm(f"P{i:06d}") for i in range(10_000)]
    share = arms.count("treatment") / len(arms)
    assert 0.48 < share < 0.52
    assert assign_arm("P000123") == assign_arm("P000123")


def test_ingest_quarantines_and_is_idempotent(landing):
    con = duckdb.connect(":memory:")
    parts = available_partitions(landing)
    assert parts[0] == "2025-01"
    total_ok = total_bad = 0
    for p in parts:
        res = ingest_partition(con, landing, p)
        total_ok += sum(r.accepted for r in res)
        total_bad += sum(r.rejected for r in res)
    assert total_bad > 0
    assert con.sql("select count(*) from raw.quarantine").fetchone()[0] == total_bad
    # second pass loads nothing: the ingestion log makes batches idempotent
    again = [r for p in parts for r in ingest_partition(con, landing, p)]
    assert again == []
    # duplicates are kept in raw (dedup happens in staging) -> raw rows > distinct ids
    rows, distinct = con.sql("select count(*), count(distinct event_id) from raw.wallet_transactions").fetchone()
    assert rows > distinct


def test_validate_lines_handles_broken_json():
    from imw.contracts import load_contracts

    version, c = load_contracts()
    ok, bad = validate_lines(c["rg_events"], ['{"payload":', ""], "2025-01", "b", version)
    assert ok == [] and len(bad) == 1 and "json" in bad[0]["errors"]
