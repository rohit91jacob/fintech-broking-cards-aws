"""Parsers against the committed fixtures (real SEC excerpts; synthetic NSE and RBI layouts)."""

import json
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from fbc.sources import nse, rbi, sec
from tests.conftest import FIXTURES


def test_sec_kpis_match_the_q2_2026_10q():
    _, kpis, gaps = sec.parse_filings(FIXTURES / "sec")
    assert not gaps
    row = next(r for r in kpis.to_pylist() if r["report_date"] == date(2026, 6, 30))
    # Robinhood Q2 2026 10-Q, Key Performance Metrics table
    assert row["funded_customers_m"] == 28.4
    assert row["platform_assets_bn"] == 368.7
    assert row["net_deposits_bn"] == 21.7
    assert row["arpu_usd"] == 187
    tenk = next(r for r in kpis.to_pylist() if r["form"] == "10-K")
    assert tenk["net_deposits_period_months"] == 12 and tenk["net_deposits_bn"] == 68.1


def test_sec_revenue_by_type_q2_2026():
    revenue, _, _ = sec.parse_filings(FIXTURES / "sec")
    q2 = {
        r["revenue_type"]: r["value_usd"] / 1e6
        for r in revenue.to_pylist()
        if r["period_start"] == date(2026, 4, 1) and r["period_end"] == date(2026, 6, 30)
    }
    assert q2 == {"transaction_based": 776, "net_interest": 389, "other": 143, "total": 1308}


def test_sec_companyfacts_keeps_selected_concepts():
    facts = sec.parse_companyfacts(next((FIXTURES / "sec").glob("companyfacts_*.json")))
    assert set(facts["concept"].to_pylist()) <= set(sec.FACT_CONCEPTS)
    assert "Revenues" in facts["concept"].to_pylist()


def test_sec_periodic_filings_sorted_newest_first():
    subs = json.loads((FIXTURES / "sec" / "submissions_CIK0001783879.json").read_text())
    filings = sec.periodic_filings(subs)
    assert filings[0].report_date >= filings[-1].report_date
    assert {f.form for f in filings} == {"10-Q", "10-K"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Funded Customers (1) (in millions) 26.5 28.4 7 % Total", 28.4),
        ("Funded Customers (1) (in millions) 25.2 27.0 Total Platform", 27.0),
        ("Funded Customers (1) (in millions) 23.0 23.4 25.2 AUC", 25.2),
    ],
)
def test_kpi_row_takes_current_period(text, expected):
    assert sec._last_value(text, sec._KPI_LABELS["funded_customers_m"]) == expected


def test_nse_parse_synthetic_layout():
    table = nse.parse_dir(FIXTURES / "nse")
    days = sorted(set(table["trade_date"].to_pylist()))
    assert len(days) == 4 and date(2026, 10, 2) not in days  # holiday has no file
    assert set(table["series"].to_pylist()) == {"EQ", "GB"}
    assert all(s.startswith("SYN") for s in table["symbol"].to_pylist())


def test_nse_file_name():
    assert nse.file_name(date(2026, 10, 9)) == "BhavCopy_NSE_CM_0_0_0_20261009_F_0000.csv.zip"


def test_rbi_psi_synthetic_layout():
    rows = rbi.parse_psi(next((FIXTURES / "rbi" / "psi").glob("*2026_08.XLSX")))
    upi = {(r["period_month"], r["measure"]) for r in rows if r["metric"] == "upi"}
    assert (date(2026, 8, 1), "volume_lakh") in upi
    assert (date(2025, 8, 1), "value_crore") in upi
    assert (date(2026, 7, 1), "volume_lakh") in upi
    counts = [r for r in rows if r["metric"] == "credit_cards_outstanding"]
    assert len(counts) == 3 and all(r["measure"] == "count_lakh" for r in counts)
    assert any(r["metric"] == "domestic_payment_frauds" for r in rows)
    # "4.1 Credit Cards" must not be confused with "4.1.1 PoS based"
    assert {r["metric"] for r in rows} >= {
        "credit_card_payments",
        "credit_card_pos",
        "credit_card_others",
    }


def test_rbi_cards_synthetic_layout_and_total():
    rows = rbi.parse_card_stats(next((FIXTURES / "rbi" / "cards").glob("*2026_08.XLSX")))
    banks = [r for r in rows if not r["is_total_row"]]
    total = next(r for r in rows if r["is_total_row"])
    assert len(banks) == 12
    assert sum(r["credit_cards_outstanding"] for r in banks) == total["credit_cards_outstanding"]
    assert {r["bank_group"] for r in banks} == set(rbi.BANK_GROUPS)
    assert all(r["period_month"] == date(2026, 8, 1) for r in rows)


def test_rbi_layout_change_is_detected(tmp_path: Path):
    wb = openpyxl.Workbook()
    wb.active.cell(
        2, 2, "ATM, Acceptance Infrastructure and Card Statistics for the Month of August 2026"
    )
    path = tmp_path / "broken.XLSX"
    wb.save(path)
    with pytest.raises(rbi.LayoutError):
        rbi.parse_card_stats(path)


def test_rbi_month_typos():
    assert rbi._month_from_text("Jauuary 2025") == date(2025, 1, 1)
    assert rbi._cell_month("2025\nAugust", None) == date(2025, 8, 1)
    assert rbi._cell_month(2026, "July") == date(2026, 7, 1)
