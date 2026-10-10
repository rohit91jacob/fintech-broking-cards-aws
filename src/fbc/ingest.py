"""Download the real public sources and land them as raw Iceberg tables.

Only SEC EDGAR serves an honestly identified automated client. NSE's download is off by default
(its terms prohibit automated collection) and the RBI's document server answers with a bot
challenge, so for those two the raw tables are built from files already in the raw directory
or, when there are none, from the committed synthetic fixtures. The ingest manifest records
which, and the results site labels it.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from fbc import http
from fbc.config import Settings
from fbc.lake import iceberg
from fbc.sources import nse, rbi, sec

log = logging.getLogger(__name__)

SOURCES = ("sec", "nse", "rbi")
MANIFEST = "ingest_manifest.json"

NSE_OFF = (
    "NSE download is off (set FBC_NSE_DOWNLOAD=1 only with NSE's permission): NSE's Terms of "
    "Use prohibit systematic or automated data collection and the use of its data for "
    "simulation, and nsearchives.nseindia.com does not answer non-browser clients"
)
FALLBACK = "continuing with files already in the raw directory, else the synthetic fixtures"


def fetch_all(settings: Settings, only: tuple[str, ...] = SOURCES) -> dict[str, Any]:
    """Network step. A failed or refused source is reported, not fatal: ``load_all`` uses what
    is in the raw directory, or the committed synthetic fixtures for NSE and RBI."""
    status: dict[str, Any] = {}
    for name in only:
        try:
            if name == "sec":
                paths = sec.fetch(settings.raw_dir, settings.sec_user_agent, settings.hood_filings)
            elif name == "nse":
                if not settings.nse_download:
                    log.warning("%s; %s", NSE_OFF, FALLBACK)
                    status[name] = {"ok": False, "skipped": NSE_OFF}
                    continue
                paths = nse.fetch(
                    settings.raw_dir, settings.trading_days, user_agent=settings.user_agent
                )
            else:
                paths = [
                    p
                    for ps in rbi.fetch(
                        settings.raw_dir, settings.rbi_months, user_agent=settings.user_agent
                    ).values()
                    for p in ps
                ]
            status[name] = {"ok": True, "files": len(paths)}
        except http.Refused as exc:
            log.warning("%s refused the download, %s: %s", name.upper(), FALLBACK, exc)
            status[name] = {"ok": False, "refused": str(exc)[:400]}
        except Exception as exc:
            log.error("fetch %s failed: %s", name, exc)
            status[name] = {"ok": False, "error": str(exc)[:300]}
    return status


def _source_dir(settings: Settings, name: str, pattern: str) -> tuple[Path, bool]:
    """The raw directory of a source if it holds files, else the committed fixtures."""
    own = settings.raw_dir / name
    if any(own.glob(pattern)):
        return own, False
    fixtures = settings.fixture_dir / name
    if any(fixtures.glob(pattern)):
        log.warning(
            "no %s files under %s: using the committed SYNTHETIC fixtures in %s",
            name.upper(),
            own,
            fixtures,
        )
        return fixtures, True
    raise FileNotFoundError(f"no {name.upper()} files under {own} (nor fixtures in {fixtures})")


def load_all(settings: Settings) -> dict[str, int]:
    """Parse the raw files into ``lake.raw_*`` tables and write the ingest manifest."""
    cat = iceberg.get_catalog(settings)
    counts: dict[str, int] = {}
    sec_dir = settings.raw_dir / "sec"
    facts_file = next(sec_dir.glob("companyfacts_*.json"), None)
    if facts_file is None:
        raise FileNotFoundError(f"no SEC companyfacts under {sec_dir}")
    counts["raw_sec_companyfacts"] = iceberg.replace_all(
        cat, "raw_sec_companyfacts", sec.parse_companyfacts(facts_file)
    )
    revenue, kpis, gaps = sec.parse_filings(sec_dir)
    counts["raw_hood_revenue_by_type"] = iceberg.replace_all(
        cat, "raw_hood_revenue_by_type", revenue
    )
    counts["raw_hood_kpis"] = iceberg.replace_all(cat, "raw_hood_kpis", kpis)

    nse_dir, nse_fallback = _source_dir(settings, "nse", "BhavCopy_NSE_CM_*.csv.zip")
    bhav = nse.parse_dir(nse_dir)
    if bhav.num_rows == 0:
        raise FileNotFoundError(f"no NSE bhavcopy rows in {nse_dir}")
    # Keep only the latest ``trading_days`` sessions so the simulation window is stable.
    available = sorted(set(bhav["trade_date"].to_pylist()))
    days = available[-settings.trading_days :]
    # The raw gate demands the full requested window. The committed fixtures are a fixed,
    # complete window of their own, so a fallback run uses all of it however many were asked.
    sessions_required = settings.trading_days
    if nse_fallback and len(available) < settings.trading_days:
        log.warning(
            "requested %d NSE sessions; the synthetic fixtures have %d (%s to %s)",
            settings.trading_days,
            len(available),
            available[0],
            available[-1],
        )
        sessions_required = len(available)
    bhav = bhav.filter(pc.is_in(bhav["trade_date"], value_set=pa.array(days)))
    counts["raw_nse_bhavcopy"] = iceberg.replace_all(cat, "raw_nse_bhavcopy", bhav, "trade_date")

    rbi_dir, rbi_fallback = _source_dir(settings, "rbi", "*/*.XLSX")
    psi, cards = rbi.parse_dir(rbi_dir)
    counts["raw_rbi_psi"] = iceberg.replace_all(cat, "raw_rbi_psi", psi)
    counts["raw_rbi_card_stats"] = iceberg.replace_all(cat, "raw_rbi_card_stats", cards)

    manifest = {
        "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": counts,
        "sec_parse_gaps": gaps,
        "nse_trading_days": [d.isoformat() for d in days],
        "nse_sessions_required": sessions_required,
        "sources": {
            "sec": {"dir": str(sec_dir), "fallback": False, "synthetic": False},
            "nse": {
                "dir": str(nse_dir),
                "fallback": nse_fallback,
                "synthetic": nse.is_synthetic(bhav),
            },
            "rbi": {
                "dir": str(rbi_dir),
                "fallback": rbi_fallback,
                "synthetic": rbi.is_synthetic(psi, cards),
            },
        },
    }
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    (settings.run_dir / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return counts


def read_manifest(settings: Settings) -> dict[str, Any]:
    path = settings.run_dir / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
