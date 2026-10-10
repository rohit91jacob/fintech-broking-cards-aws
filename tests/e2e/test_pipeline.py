"""Whole pipeline on the committed fixtures: ingest -> raw DQ -> simulate through the emulated
AWS chain -> Iceberg -> reconciliation -> lake DQ -> dbt build -> site, then a full rerun that
must reproduce exactly the same lake and marts (idempotency)."""

from __future__ import annotations

import json
import shutil

import duckdb
import pytest

from fbc.lake import iceberg
from fbc.pipeline import ingest, simulate, transform
from fbc.report import build_site

pytestmark = pytest.mark.e2e

MARTS = (
    "fct_positions_mtm",
    "mart_daily_pnl",
    "mart_settlement_cash",
    "mart_upi_growth_monthly",
    "mart_credit_card_spend_per_card",
    "mart_robinhood_kpis",
    "mart_ledger_reconciliation",
    "fct_card_fraud_alerts",
    "mart_fraud_rule_performance",
    "mart_sim_card_spend_vs_rbi",
)


def _mart_fingerprint(path) -> dict[str, object]:
    con = duckdb.connect(str(path), read_only=True)
    out: dict[str, object] = {}
    for m in MARTS:
        out[m] = con.execute(f"select count(*) from marts.{m}").fetchone()[0]
    out["pnl"] = str(con.execute("select sum(daily_pnl) from marts.mart_daily_pnl").fetchone()[0])
    out["mv"] = str(
        con.execute("select sum(market_value) from marts.fct_positions_mtm").fetchone()[0]
    )
    con.close()
    return out


def test_pipeline_end_to_end_and_rerun(fixture_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("DBT_TARGET", "ci")
    s = fixture_settings
    result = ingest(s, offline=True)
    assert result["rows"]["raw_nse_bhavcopy"] > 0
    assert result["dq"].get("failed_error", 0) == 0

    first = simulate(s)
    assert first["reconciliation_ok"], first["reconciliation"]
    assert first["ledger"]["posted"] > 100
    assert first["ledger"].get("replayed", 0) > 0  # client retries hit the idempotency table
    assert first["producer"].get("settled", 0) > 0  # T+1 settlement ran
    assert first["window"] == ["2026-09-28", "2026-10-01"]
    assert first["dq"].get("failed_error", 0) == 0

    transform(s)
    site = build_site(s, tmp_path / "site")
    summary = json.loads((site.parent / "summary.json").read_text())
    assert summary["reconciliation_ok"] is True
    assert summary["hood_funded_customers_m"] == 28.4
    html = site.read_text(encoding="utf-8")
    assert "simulated" in html and "SYN" not in html.split("<footer>")[0][:200]
    # The fixtures are synthetic NSE/RBI layouts: the site must not present them as real data.
    assert summary["sources"] == {"sec": "real", "nse": "synthetic", "rbi": "synthetic"}
    assert "not NSE data" in html and "not RBI data" in html
    assert "real · RBI" not in html and "real NSE closes" not in html

    fingerprint = _mart_fingerprint(s.duckdb_path)
    assert all(
        v for k, v in fingerprint.items() if k in MARTS and k != "mart_fraud_rule_performance"
    )
    cat = iceberg.get_catalog(s)
    lake_rows = {t: iceberg.read(cat, t).num_rows for t in ("ledger_entries", "business_events")}

    # Rerun everything with the same seed: identical lake and marts, still reconciled.
    shutil.copy(s.duckdb_path, tmp_path / "first.duckdb")
    ingest(s, offline=True)
    second = simulate(s)
    transform(s)
    assert second["reconciliation_ok"]
    assert second["ledger"] == first["ledger"]
    assert {t: iceberg.read(cat, t).num_rows for t in lake_rows} == lake_rows
    assert _mart_fingerprint(s.duckdb_path) == fingerprint


def test_ingest_gate_fails_on_missing_sources(settings):
    with pytest.raises(FileNotFoundError):
        ingest(settings, offline=True)
