"""Build the small, REAL SEC fixtures in tests/fixtures/raw/sec from a full download.

EDGAR content is public. To keep every file under 300 KB the fixtures are trimmed:
companyfacts keeps the concepts the pipeline reads, each XBRL instance keeps only its
contexts and revenue facts, and each 10-Q/10-K keeps only the Key Performance Metrics
paragraph (verbatim text). Usage:

    uv run python scripts/build_sec_fixtures.py --raw <data>/raw --out tests/fixtures/raw
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from fbc.sources import sec

KEEP_FILINGS = 6  # 10-Qs/10-K back to Q1 2025, enough to derive a Q4 from the 10-K
REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "InterestIncomeExpenseNet",
    "InterestRevenueExpenseNet",
)


def trim_companyfacts(src: Path, dst: Path) -> None:
    doc = json.loads(src.read_bytes())
    gaap = {k: v for k, v in doc["facts"]["us-gaap"].items() if k in sec.FACT_CONCEPTS}
    doc["facts"] = {"us-gaap": gaap}
    dst.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")


def trim_submissions(src: Path, dst: Path, accessions: set[str]) -> None:
    doc = json.loads(src.read_bytes())
    recent = doc["filings"]["recent"]
    idx = [i for i, a in enumerate(recent["accessionNumber"]) if a in accessions]
    doc["filings"] = {"recent": {k: [v[i] for i in idx] for k, v in recent.items()}, "files": []}
    dst.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")


def trim_instance(src: Path, dst: Path) -> None:
    text = src.read_text(encoding="utf-8")
    start = text.index("<xbrl")
    root_open = text[start : text.index(">", start) + 1]
    contexts = re.findall(r"<context\b.*?</context>", text, flags=re.S)
    tag_re = "|".join(REVENUE_TAGS)
    facts = re.findall(rf"<us-gaap:(?:{tag_re})\b[^>]*>[^<]*</us-gaap:(?:{tag_re})>", text)
    used = set(re.findall(r'contextRef="([^"]+)"', "".join(facts)))
    contexts = [c for c in contexts if re.search(r'id="([^"]+)"', c).group(1) in used]
    body = "\n".join(contexts + facts)
    dst.write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n{root_open}\n{body}\n</xbrl>\n', encoding="utf-8"
    )


def trim_primary(src: Path, dst: Path) -> None:
    text = sec.html_to_text(src.read_bytes())
    m = re.search(sec._KPI_LABELS["funded_customers_m"], text)
    excerpt = text[max(0, m.start() - 200) : m.start() + 1500] if m else ""
    dst.write_text(
        "<html><body><!-- Verbatim excerpt (Key Performance Metrics) of the filing; full document on EDGAR. -->\n<p>"
        + excerpt.replace("&", "&amp;").replace("<", "&lt;")
        + "</p></body></html>\n",
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    src, dst = args.raw / "sec", args.out / "sec"
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "filings").mkdir(parents=True)
    trim_companyfacts(
        next(src.glob("companyfacts_*.json")), dst / "companyfacts_CIK0001783879.json"
    )
    metas = sorted(
        src.glob("filings/*/filing.json"),
        key=lambda p: json.loads(p.read_text())["report_date"],
        reverse=True,
    )[:KEEP_FILINGS]
    accessions = set()
    for meta in metas:
        filing = json.loads(meta.read_text())
        accessions.add(filing["accession"])
        out = dst / "filings" / meta.parent.name
        out.mkdir()
        shutil.copy(meta, out / "filing.json")
        trim_instance(meta.parent / "instance.xml", out / "instance.xml")
        trim_primary(meta.parent / "primary.htm", out / "primary.htm")
    trim_submissions(
        next(src.glob("submissions_*.json")), dst / "submissions_CIK0001783879.json", accessions
    )
    for p in sorted(dst.rglob("*")):
        if p.is_file():
            print(f"{p.stat().st_size:>8} {p.relative_to(args.out)}")


if __name__ == "__main__":
    main()
