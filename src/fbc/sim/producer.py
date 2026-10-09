"""SIMULATED customer activity, deterministic for a given seed.

Customer orders and card swipes are not public data, so this module invents them -- every
row it produces is flagged ``simulated = true``. What is real:

* fills are priced at the real NSE volume-weighted average price of the day
  (turnover / volume from the bhavcopy, rounded to the 5-paise tick and clamped to the day's
  high-low range) for the 30 most-traded EQ symbols of the window;
* card spending is sized from the RBI's latest bank-wise card statistics: transactions per
  card per day, average PoS and e-commerce ticket sizes, the PoS/e-commerce mix and the
  issuing-bank mix of credit cards outstanding.

Everything else (customers, balances, order sizes, merchant categories, cities, the fraud
scenarios injected into card traffic, client retries) is an assumption of this simulation.
The producer talks to the ledger only through the API handler, like a mobile app would.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pyarrow as pa

from fbc.ledger.api import LedgerApi, brokerage
from fbc.sim.calibration import CardCalibration, PricePanel

CITIES = {
    "Mumbai": 0.18,
    "Delhi": 0.17,
    "Bengaluru": 0.16,
    "Hyderabad": 0.1,
    "Chennai": 0.09,
    "Pune": 0.08,
    "Kolkata": 0.07,
    "Ahmedabad": 0.06,
    "Jaipur": 0.05,
    "Kochi": 0.04,
}
# (mcc, category, weight, channel bias)  -- assumption, not RBI data
MERCHANTS = [
    ("5411", "grocery", 0.20, "POS"),
    ("5812", "restaurants", 0.14, "POS"),
    ("5541", "fuel", 0.10, "POS"),
    ("5311", "department_stores", 0.08, "POS"),
    ("5999", "online_marketplace", 0.16, "ECOM"),
    ("5732", "electronics", 0.06, "ECOM"),
    ("4511", "airlines", 0.04, "ECOM"),
    ("7011", "hotels", 0.04, "ECOM"),
    ("4900", "utilities", 0.10, "ECOM"),
    ("5691", "apparel", 0.08, "POS"),
]
RISKY = [("7995", "gambling"), ("6051", "quasi_cash_crypto")]
FRAUD_SCENARIOS = ("velocity_burst", "impossible_travel", "amount_spike", "night_risky_mcc")

CUSTOMER_SCHEMA = pa.schema(
    [
        ("customer_id", pa.string()),
        ("city", pa.string()),
        ("risk_profile", pa.string()),
        ("onboarded_on", pa.date32()),
        ("simulated", pa.bool_()),
    ]
)
CARD_SCHEMA = pa.schema(
    [
        ("card_id", pa.string()),
        ("customer_id", pa.string()),
        ("issuer_bank", pa.string()),
        ("home_city", pa.string()),
        ("credit_limit", pa.decimal128(20, 4)),
        ("statement_day", pa.int32()),
        ("simulated", pa.bool_()),
    ]
)


def _posted(out: dict[str, Any]) -> bool:
    """Posted now, or a replay of a request that was posted earlier (persistent backends)."""
    return out.get("status") == "POSTED" or out.get("original_status") == "POSTED"


def _weighted(rng: random.Random, options: dict[str, float]) -> str:
    keys = list(options)
    return rng.choices(keys, weights=[options[k] for k in keys], k=1)[0]


def _poisson(rng: random.Random, lam: float) -> int:
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


@dataclass
class Customer:
    customer_id: str
    city: str
    risk_profile: str
    cash: Decimal = Decimal(0)  # the client app's view, from API responses
    settled: dict[str, int] = field(default_factory=dict)  # isin -> qty


@dataclass
class Card:
    card_id: str
    customer_id: str
    issuer_bank: str
    home_city: str
    credit_limit: Decimal
    statement_day: int
    typical_ticket: float


@dataclass
class Fill:
    txn_id: str
    trade_date: date
    settlement_date: date
    customer_id: str
    symbol: str
    isin: str
    side: str
    qty: int
    notional: str
    fees: str


class Simulator:
    def __init__(
        self,
        api: LedgerApi,
        panel: PricePanel,
        cards_cal: CardCalibration,
        *,
        seed: int,
        customers: int,
        cards: int,
        retry_rate: float = 0.03,
        conflict_rate: float = 0.002,
        fraud_card_rate: float = 0.01,
    ) -> None:
        self.api = api
        self.panel = panel
        self.cal = cards_cal
        self.rng = random.Random(seed)
        self.retry_rate = retry_rate
        self.conflict_rate = conflict_rate
        self.fraud_card_rate = fraud_card_rate
        self.stats: Counter[str] = Counter()
        self.pending: list[Fill] = []
        start = panel.trading_days[0]
        self.customers = [
            Customer(
                f"C{i:05d}",
                _weighted(self.rng, CITIES),
                self.rng.choices(["conservative", "moderate", "active"], [0.4, 0.4, 0.2])[0],
            )
            for i in range(1, customers + 1)
        ]
        self.cards: list[Card] = []
        for i in range(1, cards + 1):
            owner = self.customers[(i - 1) % len(self.customers)]
            limit = Decimal(
                int(min(1_000_000, max(25_000, self.rng.lognormvariate(math.log(120_000), 0.6))))
                // 1000
                * 1000
            )
            self.cards.append(
                Card(
                    f"K{i:06d}",
                    owner.customer_id,
                    _weighted(self.rng, cards_cal.bank_shares),
                    owner.city,
                    limit,
                    self.rng.randint(1, 28),
                    self.rng.lognormvariate(0, 0.35),
                )
            )
        self.api.card_limits.update({c.card_id: c.credit_limit for c in self.cards})
        self.onboarded_on = start
        self.compromised = {
            c.card_id: (
                self.rng.choice(FRAUD_SCENARIOS),
                start
                + timedelta(
                    days=self.rng.randint(0, max(0, (panel.trading_days[-1] - start).days))
                ),
            )
            for c in self.cards
            if self.rng.random() < fraud_card_rate
        }

    # -------------------------------------------------------------- reference

    def customer_table(self) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "customer_id": c.customer_id,
                    "city": c.city,
                    "risk_profile": c.risk_profile,
                    "onboarded_on": self.onboarded_on,
                    "simulated": True,
                }
                for c in self.customers
            ],
            schema=CUSTOMER_SCHEMA,
        )

    def card_table(self) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "card_id": c.card_id,
                    "customer_id": c.customer_id,
                    "issuer_bank": c.issuer_bank,
                    "home_city": c.home_city,
                    "credit_limit": c.credit_limit,
                    "statement_day": c.statement_day,
                    "simulated": True,
                }
                for c in self.cards
            ],
            schema=CARD_SCHEMA,
        )

    # ---------------------------------------------------------------- calls

    def _call(self, route: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        event = {
            "routeKey": route,
            "headers": {"Idempotency-Key": key, "content-type": "application/json"},
            "body": json.dumps(body, default=str),
        }
        resp = self.api.handler(event)
        self.stats[f"http_{resp['statusCode']}"] += 1
        out = json.loads(resp["body"])
        # Client retries: the request is sent again with the same key (e.g. after a timeout).
        if self.rng.random() < self.retry_rate:
            again = self.api.handler(event)
            self.stats[f"retry_http_{again['statusCode']}"] += 1
        # A buggy client reusing a key for a different request gets 409 and is ignored.
        if self.rng.random() < self.conflict_rate and "amount" in body:
            bad = dict(body, amount=str(Decimal(str(body["amount"])) + 1))
            conflict = self.api.handler({**event, "body": json.dumps(bad, default=str)})
            self.stats[f"conflict_http_{conflict['statusCode']}"] += 1
        return out

    # ------------------------------------------------------------------- run

    def run(self) -> Counter[str]:
        first, last = self.panel.trading_days[0], self.panel.trading_days[-1]
        trading = set(self.panel.trading_days)
        day = first
        while day <= last:
            if day in trading:
                self._settle(day)
                self._deposits(day)
                self._orders(day)
            self._card_day(day)
            day += timedelta(days=1)
        self.stats["pending_settlement_at_end"] = len(self.pending)
        return self.stats

    def _ts(self, day: date, start: time, end: time) -> str:
        lo = datetime.combine(day, start)
        span = (datetime.combine(day, end) - lo).total_seconds()
        return (lo + timedelta(seconds=int(self.rng.random() * span))).isoformat()

    def _deposits(self, day: date) -> None:
        for c in self.customers:
            first_day = day == self.panel.trading_days[0]
            if not first_day and self.rng.random() > 0.03:
                continue
            median = {"conservative": 40_000, "moderate": 75_000, "active": 150_000}[c.risk_profile]
            amount = Decimal(int(self.rng.lognormvariate(math.log(median), 0.7)) // 100 * 100 + 100)
            body = {
                "customer_id": c.customer_id,
                "amount": str(amount),
                "business_date": day.isoformat(),
                "event_ts": self._ts(day, time(8, 0), time(9, 10)),
            }
            out = self._call("POST /deposits", body, f"dep:{c.customer_id}:{day}")
            if _posted(out):
                c.cash += amount

    def _orders(self, day: date) -> None:
        quotes = self.panel.quotes[day]
        settlement_date = self.panel.next_trading_day(day)
        rate = {"conservative": 0.25, "moderate": 0.6, "active": 1.4}
        for c in self.customers:
            for n in range(_poisson(self.rng, rate[c.risk_profile])):
                sellable = [isin for isin, q in c.settled.items() if q > 0]
                side = "SELL" if sellable and self.rng.random() < 0.35 else "BUY"
                if side == "BUY":
                    symbol = self.rng.choice(self.panel.universe)
                    q = quotes[symbol]
                    budget = c.cash * Decimal(str(self.rng.uniform(0.05, 0.3)))
                    if self.rng.random() < 0.02:
                        budget = c.cash * 3  # client over-orders; the ledger must refuse
                    qty = int(budget / q.vwap)
                    if qty < 1:
                        continue
                else:
                    isin = self.rng.choice(sellable)
                    q = next(x for x in quotes.values() if x.isin == isin)
                    held = c.settled[isin]
                    qty = held if self.rng.random() < 0.5 else max(1, held // 2)
                    if self.rng.random() < 0.02:
                        qty = held + 5  # more than settled holdings; the ledger must refuse
                body = {
                    "customer_id": c.customer_id,
                    "symbol": q.symbol,
                    "isin": q.isin,
                    "side": side,
                    "qty": qty,
                    "price": str(q.vwap),
                    "settlement_date": settlement_date.isoformat(),
                    "business_date": day.isoformat(),
                    "event_ts": self._ts(day, time(9, 15), time(15, 30)),
                }
                out = self._call("POST /orders", body, f"ord:{c.customer_id}:{day}:{n}")
                if not _posted(out):
                    self.stats[f"order_{out.get('status', 'ERROR').lower()}"] += 1
                    continue
                notional = Decimal(qty) * q.vwap
                fee = brokerage(notional)
                if side == "BUY":
                    c.cash -= notional + fee
                else:
                    c.settled[q.isin] -= qty
                self.pending.append(
                    Fill(
                        out["txn_id"],
                        day,
                        settlement_date,
                        c.customer_id,
                        q.symbol,
                        q.isin,
                        side,
                        qty,
                        str(notional.quantize(Decimal("0.01"))),
                        str(fee),
                    )
                )
                self.stats[f"fill_{side.lower()}"] += 1

    def _settle(self, day: date) -> None:
        """T+1 settlement job: the clearing corporation's obligations for today."""
        due = [f for f in self.pending if f.settlement_date <= day]
        self.pending = [f for f in self.pending if f.settlement_date > day]
        by_customer = {c.customer_id: c for c in self.customers}
        for f in due:
            body = {
                "trade_txn_id": f.txn_id,
                "customer_id": f.customer_id,
                "symbol": f.symbol,
                "isin": f.isin,
                "side": f.side,
                "qty": f.qty,
                "notional": f.notional,
                "fees": f.fees,
                "trade_date": f.trade_date.isoformat(),
                "business_date": day.isoformat(),
                "event_ts": datetime.combine(day, time(7, 30)).isoformat(),
            }
            out = self._call("POST /settlements", body, f"stl:{f.txn_id}")
            if not _posted(out):
                raise RuntimeError(f"settlement failed for {f.txn_id}: {out}")
            cust = by_customer[f.customer_id]
            if f.side == "BUY":
                cust.settled[f.isin] = cust.settled.get(f.isin, 0) + f.qty
            else:
                cust.cash += Decimal(f.notional) - Decimal(f.fees)
            self.stats["settled"] += 1

    # ------------------------------------------------------------------ cards

    def _amount(self, card: Card, channel: str) -> Decimal:
        mean = self.cal.avg_ticket_ecom if channel == "ECOM" else self.cal.avg_ticket_pos
        sigma = 0.9
        # lognormal with the RBI mean: mu = ln(mean) - sigma^2 / 2
        value = self.rng.lognormvariate(math.log(mean * card.typical_ticket) - sigma**2 / 2, sigma)
        return Decimal(str(max(10.0, round(value, 2))))

    def _card_day(self, day: date) -> None:
        for card in self.cards:
            events: list[dict[str, Any]] = []
            for _ in range(_poisson(self.rng, self.cal.txns_per_card_per_day)):
                mcc, category, _, bias = self.rng.choices(MERCHANTS, [m[2] for m in MERCHANTS])[0]
                channel = "ECOM" if self.rng.random() < (0.8 if bias == "ECOM" else 0.2) else "POS"
                events.append(
                    {
                        "ts": self._ts(day, time(7, 0), time(23, 30)),
                        "amount": self._amount(card, channel),
                        "mcc": mcc,
                        "merchant_category": category,
                        "channel": channel,
                        "merchant_city": card.home_city if channel == "POS" else "ONLINE",
                        "injected_fraud": False,
                        "fraud_scenario": None,
                    }
                )
            scenario = self.compromised.get(card.card_id)
            if scenario and scenario[1] == day:
                events.extend(self._fraud_events(card, day, scenario[0]))
            for n, ev in enumerate(sorted(events, key=lambda e: e["ts"])):
                body = {
                    "card_id": card.card_id,
                    "customer_id": card.customer_id,
                    "issuer_bank": card.issuer_bank,
                    "amount": str(ev["amount"]),
                    "mcc": ev["mcc"],
                    "merchant_category": ev["merchant_category"],
                    "channel": ev["channel"],
                    "merchant_city": ev["merchant_city"],
                    "injected_fraud": ev["injected_fraud"],
                    "fraud_scenario": ev["fraud_scenario"],
                    "business_date": day.isoformat(),
                    "event_ts": ev["ts"],
                }
                out = self._call(
                    "POST /card-authorizations", body, f"auth:{card.card_id}:{day}:{n}"
                )
                self.stats[f"card_auth_{out.get('status', 'ERROR').lower()}"] += 1
            if day.day == card.statement_day:
                self._pay_bill(card, day)

    def _fraud_events(self, card: Card, day: date, scenario: str) -> list[dict[str, Any]]:
        base = datetime.combine(day, time(self.rng.randint(9, 20), self.rng.randint(0, 59)))
        common = {"injected_fraud": True, "fraud_scenario": scenario}
        if scenario == "velocity_burst":
            return [
                {
                    **common,
                    "ts": (base + timedelta(seconds=40 * i)).isoformat(),
                    "amount": Decimal(str(self.rng.randint(900, 4900))),
                    "mcc": "5999",
                    "merchant_category": "online_marketplace",
                    "channel": "ECOM",
                    "merchant_city": "ONLINE",
                }
                for i in range(self.rng.randint(5, 8))
            ]
        if scenario == "impossible_travel":
            far = self.rng.choice([c for c in CITIES if c != card.home_city])
            return [
                {
                    **common,
                    "fraud_scenario": None,
                    "injected_fraud": False,
                    "ts": base.isoformat(),
                    "amount": Decimal("1250.00"),
                    "mcc": "5812",
                    "merchant_category": "restaurants",
                    "channel": "POS",
                    "merchant_city": card.home_city,
                },
                {
                    **common,
                    "ts": (base + timedelta(minutes=self.rng.randint(10, 40))).isoformat(),
                    "amount": Decimal(str(self.rng.randint(8_000, 30_000))),
                    "mcc": "5732",
                    "merchant_category": "electronics",
                    "channel": "POS",
                    "merchant_city": far,
                },
            ]
        if scenario == "amount_spike":
            spike = self.cal.avg_ticket_pos * card.typical_ticket * self.rng.uniform(12, 25)
            return [
                {
                    **common,
                    "ts": base.isoformat(),
                    "amount": Decimal(str(round(min(spike, float(card.credit_limit) * 0.9), 2))),
                    "mcc": "5944",
                    "merchant_category": "jewellery",
                    "channel": "POS",
                    "merchant_city": card.home_city,
                }
            ]
        mcc, category = self.rng.choice(RISKY)
        night = datetime.combine(day, time(self.rng.randint(1, 4), self.rng.randint(0, 59)))
        return [
            {
                **common,
                "ts": (night + timedelta(minutes=7 * i)).isoformat(),
                "amount": Decimal(str(self.rng.randint(2_000, 15_000))),
                "mcc": mcc,
                "merchant_category": category,
                "channel": "ECOM",
                "merchant_city": "ONLINE",
            }
            for i in range(self.rng.randint(2, 4))
        ]

    def _pay_bill(self, card: Card, day: date) -> None:
        resp = self.api.handler(
            {
                "routeKey": "GET /balances/{account}/{unit}",
                "pathParameters": {"account": f"CARD#{card.card_id}:OUTSTANDING", "unit": "INR"},
            }
        )
        outstanding = Decimal(json.loads(resp["body"])["balance"])
        if outstanding <= 0:
            return
        share = (
            Decimal("1")
            if self.rng.random() < 0.6
            else Decimal(str(round(self.rng.uniform(0.05, 0.9), 2)))
        )
        amount = (outstanding * share).quantize(Decimal("0.01"))
        if amount <= 0:
            return
        body = {
            "card_id": card.card_id,
            "amount": str(amount),
            "business_date": day.isoformat(),
            "event_ts": self._ts(day, time(10, 0), time(22, 0)),
        }
        out = self._call("POST /card-payments", body, f"pay:{card.card_id}:{day}")
        self.stats[f"card_payment_{out.get('status', 'ERROR').lower()}"] += 1


def simulated_counts(stats: Counter[str]) -> dict[str, int]:
    return dict(sorted(stats.items()))
