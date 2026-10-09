"""SEC EDGAR: Robinhood Markets (HOOD) XBRL company facts, revenue by type and KPI tables.

* ``companyfacts`` (data.sec.gov/api/xbrl) gives every us-gaap fact Robinhood has filed.
* Revenue by type (transaction-based, net interest, other) is a *dimensional* fact
  (``srt:ProductOrServiceAxis``), which companyfacts omits, so it is read from each 10-Q/10-K
  XBRL instance document.
* Funded Customers, Total Platform Assets (called AUC before 2025) and Net Deposits are not
  XBRL-tagged. They come from the "Key Performance Metrics" table of each 10-Q/10-K, parsed
  from the filing HTML.

EDGAR content is public. SEC requires automated clients to send a descriptive User-Agent with
a contact address and to stay under 10 requests per second.
"""

from __future__ import annotations

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa

from fbc import http

log = logging.getLogger(__name__)

HOOD_CIK = 1783879
SEC_DATA = "https://data.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

# us-gaap concepts kept from companyfacts (the full file is ~1.4 MB with 350 concepts).
FACT_CONCEPTS = (
    "Revenues",
    "NetIncomeLoss",
    "OperatingExpenses",
    "InterestExpense",
    "CashAndCashEquivalentsAtCarryingValue",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "PayablesToCustomers",
    "EarningsPerShareDiluted",
)

REVENUE_MEMBERS = {
    "hood:TransactionBasedRevenuesMember": "transaction_based",
    "hood:NetInterestRevenuesMember": "net_interest",
    "us-gaap:FinancialServiceOtherMember": "other",
}
# Older filings used a company-specific member for "Other revenues".
REVENUE_MEMBERS_ALT = {"hood:OtherRevenuesMember": "other"}

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"


def _cik10(cik: int) -> str:
    return f"CIK{cik:010d}"


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filing_date: str
    report_date: str
    primary_document: str

    @property
    def accession_nodash(self) -> str:
        return self.accession.replace("-", "")


# --------------------------------------------------------------------------------------- fetch


def fetch(raw_dir: Path, user_agent: str, max_filings: int, refresh: bool = True) -> list[Path]:
    """Download companyfacts, submissions and the latest ``max_filings`` 10-Q/10-K documents."""
    sec_dir = raw_dir / "sec"
    sess = http.session(user_agent)
    paths = []
    for name, url in (
        ("companyfacts", f"{SEC_DATA}/api/xbrl/companyfacts/{_cik10(HOOD_CIK)}.json"),
        ("submissions", f"{SEC_DATA}/submissions/{_cik10(HOOD_CIK)}.json"),
    ):
        path = sec_dir / f"{name}_{_cik10(HOOD_CIK)}.json"
        http.fetch(sess, url, path, refresh=refresh, expect=b"{")
        paths.append(path)

    for filing in periodic_filings(json.loads(paths[1].read_bytes()))[:max_filings]:
        fdir = sec_dir / "filings" / filing.accession_nodash
        base = f"{SEC_ARCHIVES}/{HOOD_CIK}/{filing.accession_nodash}"
        meta = fdir / "filing.json"
        if not meta.exists():
            index = json.loads(
                http.fetch(sess, f"{base}/index.json", fdir / "index.json", expect=b"{")
            )
            names = [item["name"] for item in index["directory"]["item"]]
            instance = next((n for n in names if n.endswith("_htm.xml")), None)
            if instance is None:
                log.warning("no XBRL instance in %s", filing.accession)
                continue
            # Immutable once filed, so cached copies never need refreshing.
            http.fetch(sess, f"{base}/{instance}", fdir / "instance.xml", expect=b"<?xml")
            http.fetch(sess, f"{base}/{filing.primary_document}", fdir / "primary.htm")
            meta.parent.mkdir(parents=True, exist_ok=True)
            meta.write_text(json.dumps(filing.__dict__, indent=2), encoding="utf-8")
        paths.append(meta)
    return paths


def periodic_filings(submissions: dict[str, Any]) -> list[Filing]:
    recent = submissions["filings"]["recent"]
    out = []
    for i, form in enumerate(recent["form"]):
        if form in ("10-Q", "10-K"):
            out.append(
                Filing(
                    accession=recent["accessionNumber"][i],
                    form=form,
                    filing_date=recent["filingDate"][i],
                    report_date=recent["reportDate"][i],
                    primary_document=recent["primaryDocument"][i],
                )
            )
    return sorted(out, key=lambda f: f.report_date, reverse=True)


# --------------------------------------------------------------------------------------- parse

FACTS_SCHEMA = pa.schema(
    [
        ("cik", pa.int64()),
        ("taxonomy", pa.string()),
        ("concept", pa.string()),
        ("unit", pa.string()),
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("value", pa.float64()),
        ("fiscal_year", pa.int32()),
        ("fiscal_period", pa.string()),
        ("form", pa.string()),
        ("accession", pa.string()),
        ("filed", pa.date32()),
        ("frame", pa.string()),
    ]
)


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_companyfacts(path: Path, concepts: tuple[str, ...] = FACT_CONCEPTS) -> pa.Table:
    doc = json.loads(path.read_bytes())
    rows: list[dict[str, Any]] = []
    for taxonomy, facts in doc["facts"].items():
        for concept, body in facts.items():
            if taxonomy == "us-gaap" and concept not in concepts:
                continue
            if taxonomy != "us-gaap":
                continue
            for unit, items in body["units"].items():
                for f in items:
                    rows.append(
                        {
                            "cik": int(doc["cik"]),
                            "taxonomy": taxonomy,
                            "concept": concept,
                            "unit": unit,
                            "period_start": _d(f.get("start")),
                            "period_end": _d(f["end"]),
                            "value": float(f["val"]),
                            "fiscal_year": f.get("fy"),
                            "fiscal_period": f.get("fp"),
                            "form": f.get("form"),
                            "accession": f.get("accn"),
                            "filed": _d(f.get("filed")),
                            "frame": f.get("frame"),
                        }
                    )
    return pa.Table.from_pylist(rows, schema=FACTS_SCHEMA)


REVENUE_SCHEMA = pa.schema(
    [
        ("accession", pa.string()),
        ("form", pa.string()),
        ("report_date", pa.date32()),
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("revenue_type", pa.string()),
        ("value_usd", pa.float64()),
    ]
)


US_GAAP = "{http://fasb.org/us-gaap/"
# Element local names -> revenue type, for contexts *without* dimensions.
UNDIMENSIONED = {
    "Revenues": "total",
    "InterestIncomeExpenseNet": "net_interest",
    "InterestRevenueExpenseNet": "net_interest",
}
# Element local names that carry the ProductOrServiceAxis split.
DIMENSIONED = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")


def parse_revenue_by_type(instance: Path, filing: Filing) -> list[dict[str, Any]]:
    """Total, transaction-based, net interest and other revenues, one row per (period, type).

    Transaction-based and other revenues are facts on ``srt:ProductOrServiceAxis``; only
    contexts with exactly that one dimension are used so cross-cuts (e.g. options revenue
    *within* transaction-based revenue) are not double counted. Net interest revenue and the
    total are undimensioned facts.
    """
    tree = ET.parse(instance)  # noqa: S314 - EDGAR XBRL instance, not user input
    root = tree.getroot()
    plain: dict[str, tuple[date, date]] = {}
    split: dict[str, tuple[date, date, str]] = {}
    for ctx in root.iter(f"{XBRLI}context"):
        start = ctx.find(f".//{XBRLI}startDate")
        end = ctx.find(f".//{XBRLI}endDate")
        if start is None or end is None:
            continue
        period = (date.fromisoformat(start.text or ""), date.fromisoformat(end.text or ""))
        members = ctx.findall(f".//{XBRLDI}explicitMember")
        if not members and ctx.find(f".//{XBRLI}segment") is None:
            plain[ctx.get("id", "")] = period
        elif len(members) == 1 and members[0].get("dimension") == "srt:ProductOrServiceAxis":
            text = (members[0].text or "").strip()
            label = REVENUE_MEMBERS.get(text) or REVENUE_MEMBERS_ALT.get(text)
            if label is not None:
                split[ctx.get("id", "")] = (*period, label)
    rows: dict[tuple[date, date, str], float] = {}
    for el in root:
        if not el.tag.startswith(US_GAAP) or not el.text:
            continue
        local = el.tag.rsplit("}", 1)[1]
        ctx_id = el.get("contextRef", "")
        if local in UNDIMENSIONED and ctx_id in plain:
            rows[(*plain[ctx_id], UNDIMENSIONED[local])] = float(el.text)
        elif local in DIMENSIONED and ctx_id in split:
            rows[split[ctx_id]] = float(el.text)
    return [
        {
            "accession": filing.accession,
            "form": filing.form,
            "report_date": date.fromisoformat(filing.report_date),
            "period_start": start,
            "period_end": end,
            "revenue_type": label,
            "value_usd": value,
        }
        for (start, end, label), value in sorted(rows.items())
    ]


KPI_SCHEMA = pa.schema(
    [
        ("accession", pa.string()),
        ("form", pa.string()),
        ("report_date", pa.date32()),
        ("funded_customers_m", pa.float64()),
        ("platform_assets_bn", pa.float64()),
        ("net_deposits_bn", pa.float64()),
        ("net_deposits_period_months", pa.int32()),
        ("arpu_usd", pa.float64()),
        ("gold_subscribers_m", pa.float64()),
    ]
)

_NUM = r"\(?\$?\s?-?[\d,]+(?:\.\d+)?\)?"
_KPI_LABELS = {
    "funded_customers_m": r"Funded Customers(?: \(\d\))? \(\s?in millions\)",
    "platform_assets_bn": r"(?:Total Platform Assets|AUC|Assets Under Custody)(?: \(\d\))? \(\s?in billions\)",
    "net_deposits_bn": r"Net Deposits(?: \(\d\))? \(\s?in billions\)",
    "arpu_usd": r"ARPU(?: \(\d\))? \(\s?in dollars\)",
    "gold_subscribers_m": r"(?:Robinhood )?Gold Subscribers(?: \(\d\))? \(\s?in millions\)",
}


def html_to_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text)


def _last_value(text: str, label: str) -> float | None:
    """The current-period value: the last number in the table row that follows ``label``."""
    match = re.search(label + r"((?:\s*" + _NUM + r")+)", text)
    if not match:
        return None
    numbers = re.findall(r"\$?\s?(-?[\d,]+(?:\.\d+)?)", match.group(1))
    # Rows end with a "% change" column in 10-Qs ("7 %"); the regex above stops before "%"
    # only when it is not adjacent, so drop a trailing integer that precedes a percent sign.
    tail = text[match.end() : match.end() + 3]
    if tail.lstrip().startswith("%") and len(numbers) > 1:
        numbers = numbers[:-1]
    return float(numbers[-1].replace(",", "")) if numbers else None


def parse_kpis(primary: Path, filing: Filing) -> dict[str, Any] | None:
    text = html_to_text(primary.read_bytes())
    start = re.search(_KPI_LABELS["funded_customers_m"], text)
    if not start:
        return None
    window = text[start.start() : start.start() + 1500]
    row: dict[str, Any] = {
        "accession": filing.accession,
        "form": filing.form,
        "report_date": date.fromisoformat(filing.report_date),
        "net_deposits_period_months": 12 if filing.form == "10-K" else 3,
    }
    for key, label in _KPI_LABELS.items():
        row[key] = _last_value(window, label)
    if row["funded_customers_m"] is None or row["platform_assets_bn"] is None:
        return None
    return row


def parse_filings(sec_dir: Path) -> tuple[pa.Table, pa.Table, list[str]]:
    """Revenue-by-type rows and KPI rows for every cached filing, plus parse gaps."""
    revenue: list[dict[str, Any]] = []
    kpis: list[dict[str, Any]] = []
    gaps: list[str] = []
    for meta in sorted((sec_dir / "filings").glob("*/filing.json")):
        filing = Filing(**json.loads(meta.read_text(encoding="utf-8")))
        instance = meta.parent / "instance.xml"
        primary = meta.parent / "primary.htm"
        if instance.exists():
            rows = parse_revenue_by_type(instance, filing)
            if not rows:
                gaps.append(f"{filing.form} {filing.report_date}: no revenue-by-type facts")
            revenue.extend(rows)
        if primary.exists():
            row = parse_kpis(primary, filing)
            if row is None:
                gaps.append(f"{filing.form} {filing.report_date}: KPI table not recognised")
            else:
                kpis.append(row)
    return (
        pa.Table.from_pylist(revenue, schema=REVENUE_SCHEMA),
        pa.Table.from_pylist(kpis, schema=KPI_SCHEMA),
        gaps,
    )
