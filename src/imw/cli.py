"""Command line entry point: ``uv run imw <command>``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from imw.config import DEFAULT_LANDING, REPORTS_DIR, GeneratorConfig, db_path


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="imw", description="iGaming metrics warehouse (SYNTHETIC DEMO DATA)")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate the synthetic landing zone")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--players", type=int, default=4000)
    g.add_argument("--landing", type=Path, default=DEFAULT_LANDING)

    ld = sub.add_parser("load", help="ingest arrival-month batches (+ dbt snapshot after each)")
    ld.add_argument("--landing", type=Path, default=DEFAULT_LANDING)
    ld.add_argument("--no-snapshot", action="store_true")
    ld.add_argument("--keep-db", action="store_true", help="do not reset the warehouse first")

    d = sub.add_parser("dbt", help="run a dbt command against the warehouse, e.g. `imw dbt build`")
    d.add_argument("args", nargs=argparse.REMAINDER)

    e = sub.add_parser("experiment", help="A/B readout of the CRM experiment")
    e.add_argument("--out", type=Path, default=REPORTS_DIR / "experiment_readout.json")

    r = sub.add_parser("report", help="build the static HTML report")
    r.add_argument("--out", type=Path, default=REPORTS_DIR / "report.html")
    r.add_argument("--img-dir", type=Path, default=None)

    a = sub.add_parser("all", help="generate -> load -> dbt build -> experiment -> report")
    a.add_argument("--seed", type=int, default=42)
    a.add_argument("--players", type=int, default=4000)

    args = p.parse_args(argv)

    if args.cmd == "generate":
        from imw.generator import generate

        m = generate(args.landing, GeneratorConfig(seed=args.seed, n_players=args.players))
        total = sum(v["events"] for v in m["injected"].values())
        print(f"[generate] {m['label']}\n[generate] {total} events -> {args.landing}")
    elif args.cmd == "load":
        from imw.pipeline import load_batches

        load_batches(args.landing, snapshot=not args.no_snapshot, reset=not args.keep_db)
    elif args.cmd == "dbt":
        from imw.pipeline import run_dbt

        run_dbt(args.args or ["build"])
    elif args.cmd == "experiment":
        _experiment(args.out)
    elif args.cmd == "report":
        from imw.report import build_report

        print(f"[report] {build_report(db_path(), args.out, args.img_dir)}")
    elif args.cmd == "all":
        from imw.generator import generate
        from imw.pipeline import load_batches, run_dbt
        from imw.report import build_report

        generate(DEFAULT_LANDING, GeneratorConfig(seed=args.seed, n_players=args.players))
        load_batches(DEFAULT_LANDING, reset=True)
        run_dbt(["build"])
        _experiment(REPORTS_DIR / "experiment_readout.json")
        print(f"[report] {build_report(db_path(), REPORTS_DIR / 'report.html')}")


def _experiment(out: Path) -> None:
    from imw.experiment import load_experiment, readout

    player, daily = load_experiment(db_path())
    res = readout(player, daily)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res["summary"], indent=2, default=str))


if __name__ == "__main__":
    main()
