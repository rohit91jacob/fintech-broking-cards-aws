"""Real-data inputs for the simulator: NSE prices and RBI credit-card statistics.

Nothing here is simulated. The *simulator* (``producer.py``) uses these numbers to price
simulated orders at real end-of-day prices and to size simulated card spending to the RBI's
published averages.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pyarrow as pa

TICK = Decimal("0.05")


@dataclass(frozen=True)
class Quote:
    symbol: str
    isin: str
    open: float
    high: float
    low: float
    close: float
    vwap: Decimal


@dataclass(frozen=True)
class PricePanel:
    trading_days: list[date]
    quotes: dict[date, dict[str, Quote]]  # day -> symbol -> quote
    universe: list[str]

    def next_trading_day(self, day: date) -> date:
        later = [d for d in self.trading_days if d > day]
        if later:
            return later[0]
        nxt = day + timedelta(days=1)
        while nxt.weekday() >= 5:  # beyond the window holidays are unknown: next weekday
            nxt += timedelta(days=1)
        return nxt


def _tick(value: float) -> Decimal:
    return (Decimal(str(value)) / TICK).quantize(Decimal(1), rounding=ROUND_HALF_UP) * TICK


def price_panel(bhavcopy: pa.Table, universe_size: int = 30, days: int | None = None) -> PricePanel:
    rows = [r for r in bhavcopy.to_pylist() if r["series"] == "EQ" and r["volume"]]
    all_days = sorted({r["trade_date"] for r in rows})
    if days:
        all_days = all_days[-days:]
    day_set = set(all_days)
    rows = [r for r in rows if r["trade_date"] in day_set]
    turnover: dict[str, float] = defaultdict(float)
    present: dict[str, set[date]] = defaultdict(set)
    for r in rows:
        turnover[r["symbol"]] += r["turnover_inr"] or 0.0
        present[r["symbol"]].add(r["trade_date"])
    # Most-traded symbols that traded on every day of the window.
    candidates = [s for s in turnover if present[s] == day_set]
    universe = sorted(candidates, key=lambda s: (-turnover[s], s))[:universe_size]
    keep = set(universe)
    quotes: dict[date, dict[str, Quote]] = defaultdict(dict)
    for r in rows:
        if r["symbol"] not in keep:
            continue
        vwap = _tick(r["turnover_inr"] / r["volume"])
        vwap = min(max(vwap, _tick(r["low"])), _tick(r["high"]))
        quotes[r["trade_date"]][r["symbol"]] = Quote(
            r["symbol"], r["isin"], r["open"], r["high"], r["low"], r["close"], vwap
        )
    return PricePanel(all_days, dict(quotes), sorted(universe))


@dataclass(frozen=True)
class CardCalibration:
    period_month: date
    cards_outstanding: float
    txns_per_card_per_day: float
    avg_ticket_pos: float
    avg_ticket_ecom: float
    ecom_share: float
    bank_shares: dict[str, float]  # issuer -> share of credit cards outstanding


def card_calibration(card_stats: pa.Table, top_banks: int = 10) -> CardCalibration:
    rows = card_stats.to_pylist()
    latest = max(r["period_month"] for r in rows)
    month_rows = [r for r in rows if r["period_month"] == latest]
    total = next(r for r in month_rows if r["is_total_row"])
    banks = [
        r for r in month_rows if not r["is_total_row"] and (r["credit_cards_outstanding"] or 0) > 0
    ]
    days = calendar.monthrange(latest.year, latest.month)[1]
    cards = total["credit_cards_outstanding"]
    pos_v, ecom_v = total["cc_pos_volume"], total["cc_ecom_volume"]
    other_v = total["cc_others_volume"] or 0.0
    banks.sort(key=lambda r: -r["credit_cards_outstanding"])
    shares = {r["bank_name"]: r["credit_cards_outstanding"] / cards for r in banks[:top_banks]}
    shares["OTHER BANKS"] = max(0.0, 1.0 - sum(shares.values()))
    return CardCalibration(
        period_month=latest,
        cards_outstanding=cards,
        txns_per_card_per_day=(pos_v + ecom_v + other_v) / cards / days,
        avg_ticket_pos=total["cc_pos_value_thousand"] * 1000 / pos_v,
        avg_ticket_ecom=total["cc_ecom_value_thousand"] * 1000 / ecom_v,
        ecom_share=ecom_v / (pos_v + ecom_v),
        bank_shares=shares,
    )
