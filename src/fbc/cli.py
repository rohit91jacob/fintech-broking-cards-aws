"""``fbc`` command line. Exit codes: 0 ok, 2 source/download error, 3 data-quality gate
failed, 4 reconciliation break, 5 dbt build failed."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from pathlib import Path

from fbc.config import Settings


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.from_env(
        data_dir=getattr(args, "data_dir", None),
        trading_days=getattr(args, "trading_days", None),
        customers=getattr(args, "customers", None),
        cards=getattr(args, "cards", None),
        seed=getattr(args, "seed", None),
    )


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_ingest(args: argparse.Namespace) -> int:
    from fbc.pipeline import ingest

    _print(ingest(_settings(args), offline=args.offline))
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from fbc.pipeline import simulate

    summary = simulate(_settings(args))
    _print(
        {
            k: summary[k]
            for k in (
                "run_id",
                "backend",
                "window",
                "producer",
                "ledger",
                "stream",
                "sink",
                "reconciliation_ok",
                "total_seconds",
            )
        }
    )
    return 0 if summary["reconciliation_ok"] else 4


def cmd_transform(args: argparse.Namespace) -> int:
    from fbc.pipeline import transform

    transform(_settings(args))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from fbc.pipeline import ingest, simulate, transform
    from fbc.report import build_site

    settings = _settings(args)
    _print({"ingest": ingest(settings, offline=args.offline)})
    summary = simulate(settings)
    if not summary["reconciliation_ok"]:
        return 4
    transform(settings)
    if args.report:
        build_site(settings, Path(args.report))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    from fbc.lake import iceberg

    settings = _settings(args)
    rows = iceberg.read(iceberg.get_catalog(settings), "recon_results").to_pylist()
    for r in rows:
        print(
            f"{r['status']:6} {r['check_name']:32} {r['scope']:40} expected={r['expected']} actual={r['actual']}"
        )
    return 0 if rows and all(r["status"] == "OK" for r in rows) else 4


def cmd_dq(args: argparse.Namespace) -> int:
    from fbc.lake import iceberg
    from fbc.quality import checks

    settings = _settings(args)
    cat = iceberg.get_catalog(settings)
    results = checks.raw_checks(cat, min_trading_days=min(settings.trading_days, 2))
    if iceberg.table_exists(cat, "recon_results"):
        recon = iceberg.read(cat, "recon_results").to_pylist()
        results += checks.lake_checks(cat, bool(recon) and all(r["status"] == "OK" for r in recon))
    for r in results:
        mark = "PASS" if r.passed else ("FAIL" if r.severity == "error" else "WARN")
        print(f"{mark:4} {r.layer:4} {r.name:36} {r.detail}")
    checks.gate(results)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from fbc.report import build_site

    out = build_site(_settings(args), Path(args.out))
    print(f"wrote {out}")
    return 0


def cmd_dbt(args: argparse.Namespace) -> int:
    from fbc.pipeline import dbt

    return dbt(_settings(args), *args.dbt_args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fbc", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--data-dir", help="defaults to $FBC_DATA_DIR or ./data")

    def sim_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--trading-days", type=int)
        sp.add_argument("--customers", type=int)
        sp.add_argument("--cards", type=int)
        sp.add_argument("--seed", type=int)

    sp = sub.add_parser(
        "ingest", help="download SEC/NSE/RBI and land raw Iceberg tables (+ raw DQ)"
    )
    common(sp)
    sim_opts(sp)
    sp.add_argument(
        "--offline", action="store_true", help="parse files already in the raw directory"
    )
    sp.set_defaults(fn=cmd_ingest)

    sp = sub.add_parser(
        "simulate",
        help="simulated orders/cards -> DynamoDB -> Streams -> Lambda -> Firehose -> Iceberg",
    )
    common(sp)
    sim_opts(sp)
    sp.set_defaults(fn=cmd_simulate)

    sp = sub.add_parser("transform", help="dbt build (DuckDB over the Iceberg tables)")
    common(sp)
    sp.set_defaults(fn=cmd_transform)

    sp = sub.add_parser("run", help="ingest + simulate + transform (+ report)")
    common(sp)
    sim_opts(sp)
    sp.add_argument("--offline", action="store_true")
    sp.add_argument("--report", metavar="DIR", help="also build the results site into DIR")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("reconcile", help="print the latest reconciliation; exit 4 on any break")
    common(sp)
    sp.set_defaults(fn=cmd_reconcile)

    sp = sub.add_parser("dq", help="re-run the raw and lake data-quality checks")
    common(sp)
    sp.set_defaults(fn=cmd_dq)

    sp = sub.add_parser("report", help="write the static results site")
    common(sp)
    sp.add_argument("--out", default="site")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("dbt", help="run any dbt command with the project's environment")
    common(sp)
    sp.add_argument("dbt_args", nargs=argparse.REMAINDER)
    sp.set_defaults(fn=cmd_dbt)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # pyiceberg warns when an overwrite filter matches nothing (first load of a partition).
    warnings.filterwarnings("ignore", message="Delete operation did not match any records")
    for noisy in ("botocore", "urllib3", "moto", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from fbc.quality.checks import QualityGateFailed

    try:
        return args.fn(args)
    except QualityGateFailed as exc:
        logging.error("data-quality gate failed: %s", exc)
        return 3
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        return 2
    except RuntimeError as exc:
        if "dbt build failed" in str(exc):
            logging.error("%s", exc)
            return 5
        raise


if __name__ == "__main__":
    sys.exit(main())
