"""End-to-end orchestration: ingest -> raw DQ -> simulate through the AWS event pipeline ->
Iceberg -> reconciliation -> lake DQ -> dbt build -> results site."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fbc.aws.clients import Aws, aws_backend
from fbc.aws.resources import ensure_resources
from fbc.config import Settings
from fbc.ingest import fetch_all, load_all, read_manifest
from fbc.lake import iceberg
from fbc.lake.sink import sink, wait_for_landing
from fbc.ledger.api import LedgerApi
from fbc.ledger.service import LedgerService
from fbc.ledger.stream import StreamPoller, handler
from fbc.quality import checks
from fbc.recon import reconcile, results_table, snapshot_table
from fbc.sim.calibration import card_calibration, price_panel
from fbc.sim.producer import Simulator

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DBT_DIR = PROJECT_ROOT / "dbt"


def raw_quality(settings: Settings) -> list[checks.CheckResult]:
    """Raw checks against the window the last ingest asked for (see ``ingest.load_all``)."""
    required = read_manifest(settings).get("nse_sessions_required", settings.trading_days)
    return checks.raw_checks(
        iceberg.get_catalog(settings),
        trading_days=required,
        session_tolerance=settings.nse_session_tolerance,
    )


def ingest(settings: Settings, *, offline: bool = False) -> dict[str, Any]:
    status = {} if offline else fetch_all(settings)
    rows = load_all(settings)
    results = raw_quality(settings)
    checks.write_report(results, settings.run_dir / "dq_raw.json")
    checks.gate(results)
    return {
        "fetch": status,
        "rows": rows,
        "sources": read_manifest(settings).get("sources", {}),
        "dq": checks.summarize(results),
    }


def simulate(settings: Settings) -> dict[str, Any]:
    """Run the simulated day-by-day activity through the (emulated) AWS event pipeline."""
    cat = iceberg.get_catalog(settings)
    panel = price_panel(iceberg.read(cat, "raw_nse_bhavcopy"), days=settings.trading_days)
    card_cal = card_calibration(iceberg.read(cat, "raw_rbi_card_stats"))
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    started = time.perf_counter()
    with aws_backend(settings) as backend:
        aws = Aws(settings)
        arns = ensure_resources(aws)
        ledger = LedgerService(aws)
        api = LedgerApi(ledger)
        sim = Simulator(
            api,
            panel,
            card_cal,
            seed=settings.seed,
            customers=settings.customers,
            cards=settings.cards,
        )
        sim_stats = sim.run()
        produce_s = time.perf_counter() - started

        poller = StreamPoller(
            aws,
            arns["journal_stream_arn"],
            lambda event, ctx: handler(event, ctx, aws),
            batch_size=100,
            redeliver_rate=0.05,
            seed=settings.seed,
        )
        stream_stats = poller.drain()
        if backend == "endpoint":
            # Emulator/AWS streams and Firehose are asynchronous: keep polling until every
            # journal item has gone through the Lambda and landed in S3.
            expected = _journal_items(aws)
            deadline = time.monotonic() + 180
            while len(poller.seen) < expected and time.monotonic() < deadline:
                time.sleep(2)
                stream_stats = poller.drain()
            if len(poller.seen) < expected:
                raise RuntimeError(
                    f"stream delivered {len(poller.seen)} of {expected} journal items"
                )
            wait_for_landing(aws, expected)
        if backend == "moto":
            # In-process moto starts empty, so this run's ledger *is* the whole history: the
            # simulated lake tables are rebuilt from it. Against a persistent endpoint
            # (LocalStack) the sink merges idempotently instead.
            for name in ("ledger_entries", "business_events"):
                iceberg.drop(cat, name)
        sink_stats = sink(aws, cat)

        balances = ledger.scan_balances()
        limits = {c.card_id: c.credit_limit for c in sim.cards}
        results = reconcile(
            balances,
            iceberg.read(cat, "ledger_entries"),
            iceberg.read(cat, "business_events"),
            limits,
        )
    recon_ok = all(c.ok for c in results)
    iceberg.replace_all(cat, "sim_customers", sim.customer_table())
    iceberg.replace_all(cat, "sim_cards", sim.card_table())
    iceberg.replace_all(cat, "ledger_balances_snapshot", snapshot_table(run_id, balances))
    iceberg.replace_all(cat, "recon_results", results_table(run_id, results))

    dq = checks.lake_checks(cat, recon_ok)
    checks.write_report(dq, settings.run_dir / "dq_lake.json")
    summary = {
        "run_id": run_id,
        "backend": backend,
        "seed": settings.seed,
        "window": [panel.trading_days[0].isoformat(), panel.trading_days[-1].isoformat()],
        "trading_days": len(panel.trading_days),
        "universe": len(panel.universe),
        "customers": settings.customers,
        "cards": settings.cards,
        "card_calibration": {
            k: (v if not isinstance(v, dict) else len(v)) for k, v in asdict(card_cal).items()
        },
        "producer": dict(sorted(sim_stats.items())),
        "ledger": dict(sorted(ledger.stats.items())),
        "stream": dict(sorted(stream_stats.items())),
        "sink": dict(sorted(sink_stats.items())),
        "reconciliation": [
            {
                "check": c.name,
                "scope": c.scope,
                "expected": str(c.expected),
                "actual": str(c.actual),
                "ok": c.ok,
            }
            for c in results
        ],
        "reconciliation_ok": recon_ok,
        "produce_seconds": round(produce_s, 1),
        "total_seconds": round(time.perf_counter() - started, 1),
        "dq": checks.summarize(dq),
    }
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    (settings.run_dir / "simulation_summary.json").write_text(
        json.dumps(summary, indent=2, default=_json_default), encoding="utf-8"
    )
    checks.gate(dq)
    return summary


def _journal_items(aws: Aws) -> int:
    total = 0
    paginator = aws.ddb.get_paginator("scan")
    for page in paginator.paginate(TableName=aws.names.journal_table, Select="COUNT"):
        total += page["Count"]
    return total


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def dbt_env(settings: Settings) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "FBC_CATALOG_URI": iceberg.catalog_properties(settings)["uri"],
            "FBC_WAREHOUSE": iceberg.catalog_properties(settings)["warehouse"],
            "FBC_DUCKDB_PATH": str(settings.duckdb_path),
            "DBT_PROFILES_DIR": str(DBT_DIR),
        }
    )
    return env


def dbt(settings: Settings, *args: str) -> int:
    exe = shutil.which("dbt", path=str(Path(sys.executable).parent)) or "dbt"
    cmd = [exe, *args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    log.info("running %s", " ".join(cmd))
    return subprocess.call(cmd, env=dbt_env(settings), cwd=DBT_DIR)  # noqa: S603


def transform(settings: Settings) -> int:
    code = dbt(settings, "build", "--target", os.environ.get("DBT_TARGET", "dev"))
    if code != 0:
        raise RuntimeError(f"dbt build failed with exit code {code}")
    return code
