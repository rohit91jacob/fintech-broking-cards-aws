"""Ledger reconciliation: the live ledger (DynamoDB balances) against independent views.

Checks
------
``trial_balance``          every unit's balances sum to zero across all accounts
``stream_completeness``    for every (account, unit), the sum of ledger entries that reached
                           Iceberg through Streams -> Lambda -> Firehose equals the DynamoDB
                           balance (nothing lost, nothing double counted)
``positions``              net filled quantity per customer and ISIN, rebuilt from the order
                           *events*, equals SEC + SEC_PENDING in the ledger
``customer_cash``          deposits - buys - fees + sells, from events, equals CASH + CASH_PENDING
``ccp_obligations``        unsettled buys/sells equal the clearing payable/receivable
``card_outstanding``       approved authorisations - bill payments per card equal OUTSTANDING
``constraints``            no constrained balance is negative or above its credit limit

Any break makes ``fbc simulate`` and ``fbc run`` (``ReconciliationBreak``) and
``fbc reconcile`` exit with status 4.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pyarrow as pa

Key = tuple[str, str]

RESULT_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("checked_at", pa.timestamp("us")),
        ("check_name", pa.string()),
        ("scope", pa.string()),
        ("expected", pa.decimal128(24, 4)),
        ("actual", pa.decimal128(24, 4)),
        ("difference", pa.decimal128(24, 4)),
        ("status", pa.string()),
    ]
)
SNAPSHOT_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("snapshot_at", pa.timestamp("us")),
        ("account", pa.string()),
        ("unit", pa.string()),
        ("balance", pa.decimal128(24, 4)),
        ("entry_count", pa.int64()),
    ]
)


@dataclass
class Check:
    name: str
    scope: str
    expected: Decimal
    actual: Decimal

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


class ReconciliationBreak(Exception):
    """At least one check broke; the CLI exits with status 4."""

    def __init__(self, breaks: list[Check]):
        self.breaks = breaks
        super().__init__(
            "; ".join(
                f"{c.name} [{c.scope}]: expected {c.expected}, actual {c.actual}" for c in breaks
            )
        )


def _d(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def reconcile(
    balances: list[dict[str, Any]],
    ledger_entries: pa.Table,
    events: pa.Table,
    card_limits: dict[str, Decimal],
) -> list[Check]:
    bal: dict[Key, Decimal] = {(b["account"], b["unit"]): _d(b.get("balance")) for b in balances}
    checks: list[Check] = []

    # 1. trial balance per unit
    per_unit: dict[str, Decimal] = defaultdict(Decimal)
    for (_, unit), value in bal.items():
        per_unit[unit] += value
    for unit, total in sorted(per_unit.items()):
        checks.append(Check("trial_balance", unit, Decimal(0), total))

    # 2. stream completeness
    from_lake: dict[Key, Decimal] = defaultdict(Decimal)
    for row in ledger_entries.select(["account", "unit", "amount"]).to_pylist():
        from_lake[(row["account"], row["unit"])] += _d(row["amount"])
    mismatched = [
        k
        for k in set(bal) | set(from_lake)
        if bal.get(k, Decimal(0)) != from_lake.get(k, Decimal(0))
    ]
    checks.append(
        Check(
            "stream_completeness",
            f"{len(set(bal) | set(from_lake))} account/unit pairs",
            Decimal(0),
            Decimal(len(mismatched)),
        )
    )
    checks.append(
        Check(
            "stream_completeness_abs_amount",
            "sum |balance| vs sum |lake|",
            sum((abs(v) for v in bal.values()), Decimal(0)),
            sum((abs(v) for v in from_lake.values()), Decimal(0)),
        )
    )

    # 3-6. rebuild positions, cash, obligations and card balances from business events
    posted = [e for e in events.to_pylist() if e["status"] == "POSTED"]
    qty: dict[Key, Decimal] = defaultdict(Decimal)
    cash: dict[str, Decimal] = defaultdict(Decimal)
    payable = Decimal(0)
    receivable = Decimal(0)
    cards: dict[str, Decimal] = defaultdict(Decimal)
    for e in posted:
        t = e["txn_type"]
        if t == "DEPOSIT":
            cash[e["customer_id"]] += _d(e["amount"])
        elif t == "TRADE_BUY":
            qty[(e["customer_id"], e["isin"])] += _d(e["qty"])
            cash[e["customer_id"]] -= _d(e["notional"]) + _d(e["fees"])
            payable += _d(e["notional"])
        elif t == "TRADE_SELL":
            qty[(e["customer_id"], e["isin"])] -= _d(e["qty"])
            cash[e["customer_id"]] += _d(e["notional"]) - _d(e["fees"])
            receivable += _d(e["notional"])
        elif t == "SETTLE_BUY":
            payable -= _d(e["notional"])
        elif t == "SETTLE_SELL":
            receivable -= _d(e["notional"])
        elif t == "CARD_AUTH":
            cards[e["card_id"]] += _d(e["amount"])
        elif t == "CARD_PAYMENT":
            cards[e["card_id"]] -= _d(e["amount"])

    def ledger(account: str, unit: str) -> Decimal:
        return bal.get((account, unit), Decimal(0))

    pos_breaks = 0
    for (cid, isin), q in qty.items():
        held = ledger(f"CUST#{cid}:SEC", isin) + ledger(f"CUST#{cid}:SEC_PENDING", isin)
        pos_breaks += held != q
    ledger_positions = {
        (a.split(":")[0][5:], u)
        for (a, u), v in bal.items()
        if a.startswith("CUST#") and ":SEC" in a and v != 0
    }
    pos_breaks += len(ledger_positions - set(qty))
    checks.append(
        Check("positions", f"{len(qty)} customer/ISIN positions", Decimal(0), Decimal(pos_breaks))
    )

    cash_breaks = sum(
        ledger(f"CUST#{cid}:CASH", "INR") + ledger(f"CUST#{cid}:CASH_PENDING", "INR") != value
        for cid, value in cash.items()
    )
    checks.append(
        Check("customer_cash", f"{len(cash)} customers", Decimal(0), Decimal(cash_breaks))
    )
    checks.append(
        Check("ccp_obligations", "CCP:CASH_PAYABLE", payable, ledger("CCP:CASH_PAYABLE", "INR"))
    )
    checks.append(
        Check(
            "ccp_obligations",
            "CCP:CASH_RECEIVABLE",
            -receivable,
            ledger("CCP:CASH_RECEIVABLE", "INR"),
        )
    )

    card_breaks = sum(ledger(f"CARD#{cid}:OUTSTANDING", "INR") != v for cid, v in cards.items())
    checks.append(
        Check("card_outstanding", f"{len(cards)} cards", Decimal(0), Decimal(card_breaks))
    )

    # 7. constraints
    violations = 0
    for (account, _unit), value in bal.items():
        kind = account.split(":", 1)[1]
        if account.startswith("CUST#") and value < 0:
            violations += 1
        if kind == "OUTSTANDING":
            card_id = account.split(":")[0][5:]
            if value < 0 or value > card_limits.get(card_id, Decimal("Infinity")):
                violations += 1
    checks.append(
        Check("constraints", "customer and card balances", Decimal(0), Decimal(violations))
    )
    return checks


def results_table(run_id: str, checks: list[Check]) -> pa.Table:
    now = datetime.now(UTC).replace(tzinfo=None)
    return pa.Table.from_pylist(
        [
            {
                "run_id": run_id,
                "checked_at": now,
                "check_name": c.name,
                "scope": c.scope,
                "expected": c.expected,
                "actual": c.actual,
                "difference": c.actual - c.expected,
                "status": "OK" if c.ok else "BREAK",
            }
            for c in checks
        ],
        schema=RESULT_SCHEMA,
    )


def snapshot_table(run_id: str, balances: list[dict[str, Any]]) -> pa.Table:
    now = datetime.now(UTC).replace(tzinfo=None)
    return pa.Table.from_pylist(
        [
            {
                "run_id": run_id,
                "snapshot_at": now,
                "account": b["account"],
                "unit": b["unit"],
                "balance": _d(b.get("balance")),
                "entry_count": int(b.get("entry_count", 0)),
            }
            for b in balances
        ],
        schema=SNAPSHOT_SCHEMA,
    )
