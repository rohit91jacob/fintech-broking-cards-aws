"""API-style producer: an API Gateway (HTTP API, payload v2) Lambda handler in front of the ledger.

Routes
------
``POST /deposits``             customer adds money over UPI
``POST /orders``               market order filled at the given price (BUY or SELL, CNC)
``POST /settlements``          T+1 settlement of an earlier fill (called by the settlement job)
``POST /card-authorizations``  credit-card purchase against the card's limit
``POST /card-payments``        card bill payment (CRED-style, paid over UPI)
``GET  /balances/{account}/{unit}``

Every write route requires an ``Idempotency-Key`` header. Status codes: 201 posted, 200 replay
of an earlier request, 422 rejected by a ledger constraint, 409 key reused with a different
body, 400 invalid request.

Brokerage is a *simulated assumption*: min(INR 20, 0.03 % of notional) per executed order.
Statutory charges (STT, exchange fees, GST, stamp duty) are not modelled.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from fbc.ledger.model import INR, Floor, Leg, Posting, Result, UnbalancedPosting, money
from fbc.ledger.service import LedgerService

BROKERAGE_CAP = Decimal("20")
BROKERAGE_RATE = Decimal("0.0003")

STATUS_CODES = {"POSTED": 201, "REPLAYED": 200, "REJECTED": 422, "CONFLICT": 409}


class BadRequest(ValueError):
    pass


def brokerage(notional: Decimal) -> Decimal:
    return money(min(BROKERAGE_CAP, notional * BROKERAGE_RATE))


def _require(body: Mapping[str, Any], *names: str) -> None:
    missing = [n for n in names if body.get(n) in (None, "")]
    if missing:
        raise BadRequest(f"missing fields: {', '.join(missing)}")


def _positive(value: Any, name: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise BadRequest(f"{name} is not a number") from exc
    if amount <= 0:
        raise BadRequest(f"{name} must be positive")
    return amount


def cust(customer_id: str, kind: str) -> str:
    return f"CUST#{customer_id}:{kind}"


def card(card_id: str) -> str:
    return f"CARD#{card_id}:OUTSTANDING"


class LedgerApi:
    def __init__(self, ledger: LedgerService, card_limits: Mapping[str, Decimal] | None = None):
        self.ledger = ledger
        # Stand-in for a lookup on the card master table.
        self.card_limits = dict(card_limits or {})
        self.routes: dict[str, Callable[[dict[str, Any], str], Posting]] = {
            "POST /deposits": self._deposit,
            "POST /orders": self._order,
            "POST /settlements": self._settlement,
            "POST /card-authorizations": self._card_authorization,
            "POST /card-payments": self._card_payment,
        }

    # API Gateway HTTP API (payload format 2.0) -> Lambda proxy response
    def handler(self, event: Mapping[str, Any], context: Any = None) -> dict[str, Any]:
        route = event.get("routeKey", "")
        try:
            if route == "GET /balances/{account}/{unit}":
                params = event.get("pathParameters") or {}
                value = self.ledger.balance(params["account"], params["unit"])
                return _response(200, {"balance": str(value)})
            build = self.routes.get(route)
            if build is None:
                return _response(404, {"message": f"no route {route}"})
            headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
            key = headers.get("idempotency-key")
            if not key:
                raise BadRequest("Idempotency-Key header is required")
            body = json.loads(event.get("body") or "{}")
            posting = build(body, key)
            result = self.ledger.post(posting)
        except (BadRequest, UnbalancedPosting, json.JSONDecodeError) as exc:
            return _response(400, {"message": str(exc)})
        return _response(STATUS_CODES[result.status], _result_body(result))

    # ------------------------------------------------------------- builders

    def _base(self, body: Mapping[str, Any], key: str, txn_type: str) -> dict[str, Any]:
        _require(body, "business_date", "event_ts")
        return {
            "txn_type": txn_type,
            "idempotency_key": key,
            "business_date": body["business_date"],
            "event_ts": body["event_ts"],
        }

    def _deposit(self, body: dict[str, Any], key: str) -> Posting:
        _require(body, "customer_id", "amount")
        amount = money(_positive(body["amount"], "amount"))
        cid = body["customer_id"]
        return Posting(
            **self._base(body, key, "DEPOSIT"),
            legs=[Leg(cust(cid, "CASH"), INR, amount), Leg("EXT:UPI_PAYIN", INR, -amount)],
            payload={"customer_id": cid, "amount": str(amount), "channel": "UPI"},
        )

    def _order(self, body: dict[str, Any], key: str) -> Posting:
        _require(body, "customer_id", "symbol", "isin", "side", "qty", "price", "settlement_date")
        side = str(body["side"]).upper()
        if side not in ("BUY", "SELL"):
            raise BadRequest("side must be BUY or SELL")
        qty = _positive(body["qty"], "qty")
        if qty != qty.to_integral_value():
            raise BadRequest("qty must be a whole number of shares")
        price = money(_positive(body["price"], "price"))
        notional = money(qty * price)
        fee = brokerage(notional)
        cid, isin = body["customer_id"], body["isin"]
        payload = {
            "customer_id": cid,
            "symbol": body["symbol"],
            "isin": isin,
            "side": side,
            "qty": str(qty),
            "price": str(price),
            "notional": str(notional),
            "fees": str(fee),
            "settlement_date": body["settlement_date"],
        }
        if side == "BUY":
            cash_out = notional + fee
            legs = [
                Leg(cust(cid, "CASH"), INR, -cash_out),
                Leg("CCP:CASH_PAYABLE", INR, notional),
                Leg("HOUSE:BROKERAGE_FEES", INR, fee),
                Leg(cust(cid, "SEC_PENDING"), isin, qty),
                Leg("CCP:SEC", isin, -qty),
            ]
            constraints = {(cust(cid, "CASH"), INR): Floor(min_after=Decimal(0))}
        else:
            legs = [
                Leg(cust(cid, "SEC"), isin, -qty),
                Leg("CCP:SEC", isin, qty),
                Leg(cust(cid, "CASH_PENDING"), INR, notional - fee),
                Leg("CCP:CASH_RECEIVABLE", INR, -notional),
                Leg("HOUSE:BROKERAGE_FEES", INR, fee),
            ]
            # Only settled shares can be sold (no short selling, no BTST in this model).
            constraints = {(cust(cid, "SEC"), isin): Floor(min_after=Decimal(0))}
        return Posting(
            **self._base(body, key, f"TRADE_{side}"),
            legs=legs,
            payload=payload,
            constraints=constraints,
        )

    def _settlement(self, body: dict[str, Any], key: str) -> Posting:
        _require(body, "trade_txn_id", "customer_id", "isin", "side", "qty", "notional", "fees")
        cid, isin = body["customer_id"], body["isin"]
        qty = _positive(body["qty"], "qty")
        notional = money(_positive(body["notional"], "notional"))
        fee = money(Decimal(str(body["fees"])))
        side = str(body["side"]).upper()
        if side == "BUY":
            legs = [
                Leg(cust(cid, "SEC_PENDING"), isin, -qty),
                Leg(cust(cid, "SEC"), isin, qty),
                Leg("CCP:CASH_PAYABLE", INR, -notional),
                Leg("EXT:CCP_SETTLEMENT", INR, notional),
            ]
            constraints = {(cust(cid, "SEC_PENDING"), isin): Floor(min_after=Decimal(0))}
        else:
            proceeds = notional - fee
            legs = [
                Leg(cust(cid, "CASH_PENDING"), INR, -proceeds),
                Leg(cust(cid, "CASH"), INR, proceeds),
                Leg("CCP:CASH_RECEIVABLE", INR, notional),
                Leg("EXT:CCP_SETTLEMENT", INR, -notional),
            ]
            constraints = {(cust(cid, "CASH_PENDING"), INR): Floor(min_after=Decimal(0))}
        payload = {
            k: body[k]
            for k in (
                "trade_txn_id",
                "customer_id",
                "symbol",
                "isin",
                "side",
                "qty",
                "notional",
                "fees",
                "trade_date",
            )
            if k in body
        }
        return Posting(
            **self._base(body, key, f"SETTLE_{side}"),
            legs=legs,
            payload=payload,
            constraints=constraints,
        )

    def _card_authorization(self, body: dict[str, Any], key: str) -> Posting:
        _require(body, "card_id", "amount")
        card_id = body["card_id"]
        if card_id not in self.card_limits:
            raise BadRequest(f"unknown card {card_id}")
        amount = money(_positive(body["amount"], "amount"))
        payload = {k: v for k, v in body.items() if k not in ("business_date", "event_ts")}
        payload["amount"] = str(amount)
        return Posting(
            **self._base(body, key, "CARD_AUTH"),
            legs=[Leg(card(card_id), INR, amount), Leg("EXT:CARD_NETWORK", INR, -amount)],
            payload=payload,
            constraints={(card(card_id), INR): Floor(max_after=self.card_limits[card_id])},
        )

    def _card_payment(self, body: dict[str, Any], key: str) -> Posting:
        _require(body, "card_id", "amount")
        card_id = body["card_id"]
        amount = money(_positive(body["amount"], "amount"))
        return Posting(
            **self._base(body, key, "CARD_PAYMENT"),
            legs=[Leg(card(card_id), INR, -amount), Leg("EXT:UPI_PAYIN", INR, amount)],
            payload={"card_id": card_id, "amount": str(amount), "channel": "UPI"},
            constraints={(card(card_id), INR): Floor(min_after=Decimal(0))},
        )


def _result_body(result: Result) -> dict[str, Any]:
    body: dict[str, Any] = {"status": result.status, "txn_id": result.txn_id}
    if result.reason:
        body["reason"] = result.reason
    if result.original_status:
        body["original_status"] = result.original_status
    return body


def _response(status: int, body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
