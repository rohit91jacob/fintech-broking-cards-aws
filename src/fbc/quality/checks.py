"""Data-quality gates between layers.

``raw`` runs after ingest (before anything is simulated or transformed), ``lake`` after the
event pipeline has landed the ledger in Iceberg. dbt tests gate the marts. An ``error``
failure stops the pipeline (exit status 3); a ``warn`` is reported and kept in the summary.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog import Catalog

from fbc.lake import iceberg


@dataclass(frozen=True)
class CheckResult:
    layer: str
    name: str
    severity: str  # error | warn
    passed: bool
    detail: str


class QualityGateFailed(Exception):
    def __init__(self, failures: list[CheckResult]):
        self.failures = failures
        super().__init__("; ".join(f"{f.layer}.{f.name}: {f.detail}" for f in failures))


def _con(**tables: pa.Table) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for name, table in tables.items():
        con.register(name, table)
    return con


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    row = con.execute(sql).fetchone()
    return row[0] if row else None


def raw_checks(cat: Catalog, min_trading_days: int, today: date | None = None) -> list[CheckResult]:
    today = today or date.today()
    t = {
        name: iceberg.read(cat, name)
        for name in (
            "raw_sec_companyfacts",
            "raw_hood_revenue_by_type",
            "raw_hood_kpis",
            "raw_nse_bhavcopy",
            "raw_rbi_psi",
            "raw_rbi_card_stats",
        )
    }
    con = _con(**t)
    out: list[CheckResult] = []

    def add(name: str, passed: bool, detail: str, severity: str = "error") -> None:
        out.append(CheckResult("raw", name, severity, bool(passed), detail))

    n = _one(con, "select count(*) from raw_sec_companyfacts where concept = 'Revenues'")
    add("sec_revenues_present", n and n > 0, f"{n} Revenues facts")

    # Revenue components must add up to total revenue for each quarter.
    rows = con.execute(
        """
        with q as (
          select period_start, period_end, revenue_type, max(value_usd) v
          from raw_hood_revenue_by_type
          where date_diff('day', period_start, period_end) between 80 and 100
          group by all)
        select period_end,
               sum(v) filter (where revenue_type <> 'total') parts,
               max(v) filter (where revenue_type = 'total') total
        from q group by period_end order by period_end
        """
    ).fetchall()
    bad = [r for r in rows if r[1] is None or r[2] is None or abs(r[1] - r[2]) > 1e6]
    latest_bad = bool(rows) and rows[-1] in bad
    add("hood_revenue_parts_sum_to_total", not latest_bad, f"latest quarter ok={not latest_bad}")
    add(
        "hood_revenue_parts_all_quarters",
        not bad,
        f"{len(bad)} of {len(rows)} quarters do not add up: {[str(r[0]) for r in bad]}",
        "warn",
    )

    k = con.execute(
        "select count(*), min(funded_customers_m), max(funded_customers_m), max(report_date) from raw_hood_kpis"
    ).fetchone()
    add("hood_kpis_rows", k[0] >= 4, f"{k[0]} KPI rows")
    add("hood_kpis_range", k[0] and 1 <= k[1] <= k[2] <= 200, f"funded customers {k[1]}..{k[2]} M")
    if k[3]:
        age = (today - k[3]).days
        add("hood_kpis_fresh", age <= 200, f"latest period {k[3]} is {age} days old", "warn")

    days = _one(con, "select count(distinct trade_date) from raw_nse_bhavcopy")
    add("nse_trading_days", days >= min_trading_days, f"{days} sessions (need {min_trading_days})")
    dup = _one(
        con,
        "select count(*) from (select trade_date, symbol, series from raw_nse_bhavcopy group by all having count(*) > 1)",
    )
    add("nse_unique_symbol_day", dup == 0, f"{dup} duplicate (date, symbol, series)")
    bad_px = _one(
        con,
        """select count(*) from raw_nse_bhavcopy where series = 'EQ' and volume > 0 and
           (close is null or high < low or close > high + 1e-6 or close < low - 1e-6)""",
    )
    add("nse_price_sanity", bad_px == 0, f"{bad_px} EQ rows with inconsistent OHLC")
    eq = _one(con, "select count(distinct symbol) from raw_nse_bhavcopy where series = 'EQ'")
    add("nse_eq_universe", eq >= 30, f"{eq} EQ symbols")

    upi_months = _one(
        con,
        "select count(distinct period_month) from raw_rbi_psi where metric = 'upi' and measure = 'volume_lakh'",
    )
    add("rbi_upi_months", upi_months >= 3, f"{upi_months} months of UPI")
    neg = _one(con, "select count(*) from raw_rbi_psi where value < 0")
    add("rbi_psi_non_negative", neg == 0, f"{neg} negative values")
    # The same month published in two releases may be revised; flag large revisions.
    rev = con.execute(
        """select metric, measure, period_month, max(value) / nullif(min(value), 0) - 1 r
           from raw_rbi_psi where metric = 'upi' group by all having count(*) > 1 and r > 0.02"""
    ).fetchall()
    add("rbi_psi_revisions_small", not rev, f"{len(rev)} UPI month(s) revised by >2%", "warn")

    recon = con.execute(
        """
        with banks as (
          select period_month, sum(credit_cards_outstanding) s from raw_rbi_card_stats
          where not is_total_row group by all),
        total as (select period_month, credit_cards_outstanding t from raw_rbi_card_stats where is_total_row)
        select b.period_month, s, t from banks b join total using (period_month)
        where abs(s - t) > 0.005 * t
        """
    ).fetchall()
    months = _one(con, "select count(distinct period_month) from raw_rbi_card_stats")
    add(
        "rbi_cards_banks_sum_to_total",
        not recon,
        f"{months} months, {len(recon)} with bank sum != total",
    )
    return out


def lake_checks(cat: Catalog, recon_ok: bool) -> list[CheckResult]:
    entries = iceberg.read(cat, "ledger_entries")
    events = iceberg.read(cat, "business_events")
    con = _con(entries=entries, events=events)
    out: list[CheckResult] = []

    def add(name: str, passed: bool, detail: str, severity: str = "error") -> None:
        out.append(CheckResult("lake", name, severity, bool(passed), detail))

    unbalanced = _one(
        con,
        "select count(*) from (select txn_id, unit from entries group by all having sum(amount) <> 0)",
    )
    add(
        "every_txn_balances", unbalanced == 0, f"{unbalanced} (txn, unit) groups do not sum to zero"
    )
    dup = _one(con, "select count(*) - count(distinct (txn_id, leg_no)) from entries")
    add("ledger_entries_unique", dup == 0, f"{dup} duplicate legs")
    dup_e = _one(con, "select count(*) - count(distinct txn_id) from events")
    add("business_events_unique", dup_e == 0, f"{dup_e} duplicate events")
    orphan = _one(
        con,
        """select count(*) from events e where status = 'POSTED'
           and not exists (select 1 from entries l where l.txn_id = e.txn_id)""",
    )
    add("posted_events_have_legs", orphan == 0, f"{orphan} posted events without legs")
    rejected_with_legs = _one(
        con,
        """select count(*) from events e where status = 'REJECTED'
           and exists (select 1 from entries l where l.txn_id = e.txn_id)""",
    )
    add(
        "rejected_events_have_no_legs",
        rejected_with_legs == 0,
        f"{rejected_with_legs} rejected with legs",
    )
    labelled = _one(con, "select count(*) from events where simulated is distinct from true")
    add("events_labelled_simulated", labelled == 0, f"{labelled} events not flagged simulated")
    add("reconciliation", recon_ok, "ledger balances == positions, cash, obligations, cards")
    return out


def gate(results: list[CheckResult]) -> None:
    failures = [r for r in results if r.severity == "error" and not r.passed]
    if failures:
        raise QualityGateFailed(failures)


def write_report(results: list[CheckResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(r) for r in results], indent=2, default=str), encoding="utf-8"
    )


def summarize(results: list[CheckResult]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in results:
        counts["passed" if r.passed else f"failed_{r.severity}"] += 1
    return dict(counts)
