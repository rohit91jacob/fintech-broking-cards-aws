"""Download the real public sources and land them as raw Iceberg tables."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from fbc.config import Settings
from fbc.lake import iceberg
from fbc.sources import nse, rbi, sec

log = logging.getLogger(__name__)

SOURCES = ("sec", "nse", "rbi")


def fetch_all(settings: Settings, only: tuple[str, ...] = SOURCES) -> dict[str, Any]:
    """Network step. Failures of one source are reported, not fatal, when a cached copy exists."""
    status: dict[str, Any] = {}
    for name in only:
        try:
            if name == "sec":
                paths = sec.fetch(settings.raw_dir, settings.sec_user_agent, settings.hood_filings)
            elif name == "nse":
                paths = nse.fetch(settings.raw_dir, settings.trading_days)
            else:
                paths = [
                    p
                    for ps in rbi.fetch(settings.raw_dir, settings.rbi_months).values()
                    for p in ps
                ]
            status[name] = {"ok": True, "files": len(paths)}
        except Exception as exc:
            log.error("fetch %s failed: %s", name, exc)
            status[name] = {"ok": False, "error": str(exc)[:300]}
    return status


def load_all(settings: Settings) -> dict[str, int]:
    """Parse whatever is in the raw directory into ``lake.raw_*`` tables."""
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

    bhav = nse.parse_dir(settings.raw_dir / "nse")
    if bhav.num_rows == 0:
        raise FileNotFoundError("no NSE bhavcopy files in the raw directory")
    # Keep only the latest ``trading_days`` sessions so the simulation window is stable.
    days = sorted(set(bhav["trade_date"].to_pylist()))[-settings.trading_days :]
    bhav = bhav.filter(pc.is_in(bhav["trade_date"], value_set=pa.array(days)))
    counts["raw_nse_bhavcopy"] = iceberg.replace_all(cat, "raw_nse_bhavcopy", bhav, "trade_date")

    psi, cards = rbi.parse_dir(settings.raw_dir / "rbi")
    counts["raw_rbi_psi"] = iceberg.replace_all(cat, "raw_rbi_psi", psi)
    counts["raw_rbi_card_stats"] = iceberg.replace_all(cat, "raw_rbi_card_stats", cards)

    manifest = {
        "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": counts,
        "sec_parse_gaps": gaps,
        "nse_trading_days": [d.isoformat() for d in days],
    }
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    (settings.run_dir / "ingest_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return counts
