"""Double-entry ledger model.

Every posting is a set of legs. Each leg moves a signed ``amount`` of one ``unit`` (``INR`` or
an NSE ISIN for share quantities) into an ``account``. The invariant -- checked before any
write and again by the reconciliation job -- is that the legs of a posting sum to zero for
every unit, so the trial balance of the whole ledger is zero per unit.

Account naming (``<owner>:<kind>``):

=========================  ===============================================================
``CUST#<id>:CASH``         customer's free cash at the broker (INR)
``CUST#<id>:CASH_PENDING`` sale proceeds awaiting T+1 settlement (INR)
``CUST#<id>:SEC``          settled shares (unit = ISIN)
``CUST#<id>:SEC_PENDING``  bought shares awaiting T+1 settlement (unit = ISIN)
``CARD#<id>:OUTSTANDING``  credit-card balance owed by the cardholder (INR)
``CCP:CASH_PAYABLE``       broker's net cash obligation to the clearing corporation
``CCP:CASH_RECEIVABLE``    clearing corporation's cash due to the broker
``CCP:SEC``                share contra account against the market (unit = ISIN)
``HOUSE:BROKERAGE_FEES``   broker fee income (INR, credit balance)
``EXT:UPI_PAYIN``          money arriving from customers' bank accounts over UPI
``EXT:CCP_SETTLEMENT``     cash exchanged with the clearing corporation at settlement
``EXT:CARD_NETWORK``       merchant settlement through the card network
=========================  ===============================================================
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

INR = "INR"
TXN_NAMESPACE = uuid.UUID("6f1c2a52-0f8e-4a8e-9a43-6e3c1b0d9f17")
Q2 = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Q2, rounding=ROUND_HALF_UP)


class UnbalancedPosting(ValueError):
    """The legs of a posting do not sum to zero for some unit."""


@dataclass(frozen=True)
class Leg:
    account: str
    unit: str
    amount: Decimal

    @property
    def owner(self) -> str:
        return self.account.split(":", 1)[0]

    @property
    def kind(self) -> str:
        return self.account.split(":", 1)[1]


@dataclass(frozen=True)
class Floor:
    """Balance constraint checked atomically with the write.

    ``min_after``: the account balance after this posting must be >= the value.
    ``max_after``: the account balance after this posting must be <= the value.
    """

    min_after: Decimal | None = None
    max_after: Decimal | None = None


@dataclass
class Posting:
    txn_type: str
    idempotency_key: str
    business_date: str
    event_ts: str
    legs: list[Leg]
    payload: dict[str, Any] = field(default_factory=dict)
    constraints: dict[tuple[str, str], Floor] = field(default_factory=dict)

    @property
    def txn_id(self) -> str:
        return str(uuid.uuid5(TXN_NAMESPACE, self.idempotency_key))

    def request_hash(self) -> str:
        body = {
            "txn_type": self.txn_type,
            "legs": [(leg.account, leg.unit, str(leg.amount)) for leg in self.legs],
            "payload": self.payload,
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def validate(self) -> None:
        if not self.legs:
            raise UnbalancedPosting(f"{self.txn_type}: no legs")
        totals: dict[str, Decimal] = defaultdict(Decimal)
        seen: set[tuple[str, str]] = set()
        for leg in self.legs:
            if (leg.account, leg.unit) in seen:
                raise UnbalancedPosting(f"{self.txn_type}: duplicate leg {leg.account}/{leg.unit}")
            seen.add((leg.account, leg.unit))
            totals[leg.unit] += leg.amount
        bad = {unit: total for unit, total in totals.items() if total != 0}
        if bad:
            raise UnbalancedPosting(f"{self.txn_type}: legs do not balance: {bad}")
        for key in self.constraints:
            if key not in seen:
                raise UnbalancedPosting(f"{self.txn_type}: constraint on {key} without a leg")


@dataclass(frozen=True)
class Result:
    status: str  # POSTED | REPLAYED | REJECTED | CONFLICT
    txn_id: str
    reason: str | None = None
    original_status: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "POSTED" or (
            self.status == "REPLAYED" and self.original_status == "POSTED"
        )
