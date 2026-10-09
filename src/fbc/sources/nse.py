"""NSE India capital-market bhavcopy (end-of-day prices), UDiFF format (July 2024 onwards).

URL pattern: ``nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip``.
A 404 means no trading session (weekend or exchange holiday), which is how the trading
calendar is discovered. NSE does not license its data for redistribution: files are
downloaded at run time into the (git-ignored) raw directory and are never committed or
published. Tests use a *synthetic* file in the same layout (tests/fixtures/synthetic/nse).
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa

from fbc import http

log = logging.getLogger(__name__)

URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
FILE_RE = re.compile(r"BhavCopy_NSE_CM_0_0_0_(\d{8})_F_0000\.csv\.zip$")

SCHEMA = pa.schema(
    [
        ("trade_date", pa.date32()),
        ("symbol", pa.string()),
        ("isin", pa.string()),
        ("series", pa.string()),
        ("instrument_name", pa.string()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("prev_close", pa.float64()),
        ("volume", pa.int64()),
        ("turnover_inr", pa.float64()),
        ("trades", pa.int64()),
        ("source_file", pa.string()),
    ]
)


def file_name(day: date) -> str:
    return f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def fetch(
    raw_dir: Path, trading_days: int, end: date | None = None, max_lookback: int = 40
) -> list[Path]:
    """Download the latest ``trading_days`` bhavcopies on or before ``end`` (default today)."""
    out_dir = raw_dir / "nse"
    sess = http.session()
    day = end or date.today()
    found: list[Path] = []
    for _ in range(max_lookback):
        if len(found) >= trading_days:
            break
        if day.weekday() < 5:
            path = out_dir / file_name(day)
            try:
                # A past session's bhavcopy is final, so cached copies are reused.
                http.fetch(sess, URL.format(d=day), path, expect=b"PK", retries=3)
                found.append(path)
            except http.NotFound:
                log.info("no NSE session on %s", day)
        day -= timedelta(days=1)
    if len(found) < trading_days:
        raise RuntimeError(f"only {len(found)} NSE bhavcopies found, wanted {trading_days}")
    return sorted(found)


def _f(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


def _i(value: str) -> int | None:
    value = value.strip()
    return int(float(value)) if value else None


def parse_file(path: Path) -> list[dict[str, object]]:
    with zipfile.ZipFile(path) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = zf.read(member).decode("utf-8-sig")
    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        if rec.get("FinInstrmTp") != "STK":
            continue
        rows.append(
            {
                "trade_date": date.fromisoformat(rec["TradDt"]),
                "symbol": rec["TckrSymb"].strip(),
                "isin": rec["ISIN"].strip(),
                "series": rec["SctySrs"].strip(),
                "instrument_name": rec["FinInstrmNm"].strip(),
                "open": _f(rec["OpnPric"]),
                "high": _f(rec["HghPric"]),
                "low": _f(rec["LwPric"]),
                "close": _f(rec["ClsPric"]),
                "prev_close": _f(rec["PrvsClsgPric"]),
                "volume": _i(rec["TtlTradgVol"]),
                "turnover_inr": _f(rec["TtlTrfVal"]),
                "trades": _i(rec["TtlNbOfTxsExctd"]),
                "source_file": path.name,
            }
        )
    return rows


def parse_dir(nse_dir: Path) -> pa.Table:
    rows: list[dict[str, object]] = []
    for path in sorted(nse_dir.glob("BhavCopy_NSE_CM_*.csv.zip")):
        rows.extend(parse_file(path))
    return pa.Table.from_pylist(rows, schema=SCHEMA)
