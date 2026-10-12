"""Write postings to DynamoDB atomically, exactly once per idempotency key.

One ``TransactWriteItems`` call per posting contains:

1. ``Put`` on the idempotency table with ``attribute_not_exists`` -- a retried request fails here;
2. ``Put`` of the journal header (``sk = "H"``) and one ``Put`` per leg (``sk = "L#001"``...),
   each with ``attribute_not_exists(txn_id)``. ``txn_id`` is derived from the idempotency key,
   so a retry that arrives after its idempotency record has expired (30-day TTL) fails here;
3. ``Update ... ADD balance :amount`` on the balances table for each leg, with a
   ``ConditionExpression`` for constrained accounts (no overdraft, credit limit, no short sale).

If any condition fails DynamoDB cancels the whole transaction. The service then tells the
cases apart from the cancellation reasons: an existing idempotency record or journal entry
makes the request a replay (the original outcome is returned) or, with a different body, a
conflict; a failed balance condition becomes a ``REJECTED`` header, written with its own
idempotency record so that retrying a rejected request is also stable. The journal header
keeps the request hash, so it answers retries once the idempotency record has expired.
The journal table's stream (NEW_IMAGE) feeds the Lambda that ships entries to Firehose.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from fbc.aws.clients import Aws
from fbc.ledger.model import Floor, Leg, Posting, Result

log = logging.getLogger(__name__)

_ser = TypeSerializer()
_de = TypeDeserializer()


def _item(values: dict[str, Any]) -> dict[str, Any]:
    return {k: _ser.serialize(v) for k, v in values.items() if v is not None}


def _plain(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _de.deserialize(v) for k, v in item.items()}


def _reason_codes(exc: Exception) -> list[str]:
    response = getattr(exc, "response", {}) or {}
    reasons = response.get("CancellationReasons")
    if reasons:
        return [r.get("Code", "None") for r in reasons]
    # LocalStack/moto put the codes in the message: "... [ConditionalCheckFailed, None, ...]"
    message = str(response.get("Error", {}).get("Message", exc))
    if "[" in message:
        inner = message[message.rfind("[") + 1 : message.rfind("]")]
        return [c.strip() for c in inner.split(",")]
    return []


# Cancellation codes that describe the request, not a transient condition. Anything else
# (TransactionConflict, ThrottlingError, ProvisionedThroughputExceeded, ...) means nothing was
# written and the same request can simply be retried with the same idempotency key.
_DEFINITIVE_CODES = {"None", "ConditionalCheckFailed"}


class TransientLedgerError(RuntimeError):
    """The transaction was cancelled for a retryable reason; nothing was written."""

    def __init__(self, codes: list[str]):
        self.codes = codes
        super().__init__(f"transaction cancelled, retry: {codes}")


class LedgerService:
    def __init__(self, aws: Aws) -> None:
        self.aws = aws
        self.n = aws.names
        self.stats: Counter[str] = Counter()

    # ------------------------------------------------------------------ writes

    def post(self, posting: Posting) -> Result:
        posting.validate()
        txn_id = posting.txn_id
        request_hash = posting.request_hash()
        now = datetime.now(UTC).isoformat()
        items: list[dict[str, Any]] = [
            self._idempotency_put(posting, txn_id, request_hash, "POSTED", now),
            self._journal_put(self._header(posting, request_hash, "POSTED")),
        ]
        for i, leg in enumerate(posting.legs, start=1):
            items.append(self._journal_put(self._leg(posting, leg, i)))
        for leg in posting.legs:
            items.append(self._balance_update(posting, leg, now))

        try:
            self.aws.ddb.transact_write_items(TransactItems=items)
        except self.aws.ddb.exceptions.TransactionCanceledException as exc:
            codes = _reason_codes(exc)
            if any(code not in _DEFINITIVE_CODES for code in codes):
                # Do NOT record a rejection for a conflict or throttle: that would turn a
                # transient failure into a permanent answer for this idempotency key.
                self.stats["transient"] += 1
                raise TransientLedgerError(codes) from exc
            # Items before ``first_balance`` are the idempotency record and the journal items.
            # A failed condition on any of them means the key was used before: its record is
            # still there or, after the TTL removed it, the journal items it wrote are.
            first_balance = 2 + len(posting.legs)
            if "ConditionalCheckFailed" in codes[:first_balance]:
                return self._replay(posting, request_hash)
            failed = [
                posting.legs[i - first_balance]
                for i, code in enumerate(codes)
                if code == "ConditionalCheckFailed" and i >= first_balance
            ]
            reason = (
                "insufficient balance or limit on "
                + ",".join(f"{leg.account}/{leg.unit}" for leg in failed)
                if failed
                else "condition failed"
            )
            return self._reject(posting, request_hash, reason)
        self.stats["posted"] += 1
        return Result("POSTED", txn_id)

    def _reject(self, posting: Posting, request_hash: str, reason: str) -> Result:
        now = datetime.now(UTC).isoformat()
        header = self._header(posting, request_hash, "REJECTED", reason)
        try:
            self.aws.ddb.transact_write_items(
                TransactItems=[
                    self._idempotency_put(posting, posting.txn_id, request_hash, "REJECTED", now),
                    self._journal_put(header),
                ]
            )
        except self.aws.ddb.exceptions.TransactionCanceledException as exc:
            codes = _reason_codes(exc)
            if any(code not in _DEFINITIVE_CODES for code in codes):
                # Nothing was written, so there is no rejection to replay: let the caller retry.
                self.stats["transient"] += 1
                raise TransientLedgerError(codes) from exc
            return self._replay(posting, request_hash)
        self.stats["rejected"] += 1
        return Result("REJECTED", posting.txn_id, reason)

    def _replay(self, posting: Posting, request_hash: str) -> Result:
        """The first outcome for a key used before: from its idempotency record or, once the
        TTL has removed that, from the journal header written in the same transaction."""
        stored = self._get(self.n.idempotency_table, {"idempotency_key": posting.idempotency_key})
        if stored is None:
            stored = self._get(self.n.journal_table, {"txn_id": posting.txn_id, "sk": "H"})
        if stored is None:  # pragma: no cover - journal items are never deleted
            raise RuntimeError(f"no record of idempotency key {posting.idempotency_key}")
        if stored.get("request_hash") != request_hash:
            self.stats["conflict"] += 1
            return Result(
                "CONFLICT", stored["txn_id"], "idempotency key reused with a different request"
            )
        self.stats["replayed"] += 1
        return Result("REPLAYED", stored["txn_id"], original_status=stored["status"])

    def _get(self, table: str, key: dict[str, Any]) -> dict[str, Any] | None:
        item = self.aws.ddb.get_item(TableName=table, Key=_item(key), ConsistentRead=True)
        return _plain(item["Item"]) if "Item" in item else None

    def _journal_put(self, item: dict[str, Any]) -> dict[str, Any]:
        # Journal items are written once. txn_id is derived from the idempotency key, so a key
        # used before fails this condition even after its idempotency record has expired.
        return {
            "Put": {
                "TableName": self.n.journal_table,
                "Item": item,
                "ConditionExpression": "attribute_not_exists(txn_id)",
            }
        }

    def _idempotency_put(
        self, posting: Posting, txn_id: str, request_hash: str, status: str, now: str
    ) -> dict[str, Any]:
        return {
            "Put": {
                "TableName": self.n.idempotency_table,
                "Item": _item(
                    {
                        "idempotency_key": posting.idempotency_key,
                        "txn_id": txn_id,
                        "request_hash": request_hash,
                        "status": status,
                        "txn_type": posting.txn_type,
                        "created_at": now,
                        # DynamoDB TTL attribute: records are kept for 30 days; the journal
                        # header answers later retries (``_replay``).
                        "expires_at": int(datetime.now(UTC).timestamp()) + 30 * 86400,
                    }
                ),
                "ConditionExpression": "attribute_not_exists(idempotency_key)",
            }
        }

    def _header(
        self, posting: Posting, request_hash: str, status: str, reason: str | None = None
    ) -> dict[str, Any]:
        return _item(
            {
                "txn_id": posting.txn_id,
                "sk": "H",
                "record_type": "HEADER",
                "txn_type": posting.txn_type,
                "status": status,
                "reason": reason,
                "business_date": posting.business_date,
                "event_ts": posting.event_ts,
                "idempotency_key": posting.idempotency_key,
                "request_hash": request_hash,
                "leg_count": len(posting.legs) if status == "POSTED" else 0,
                "payload": json.dumps(posting.payload, sort_keys=True, default=str),
            }
        )

    def _leg(self, posting: Posting, leg: Leg, number: int) -> dict[str, Any]:
        return _item(
            {
                "txn_id": posting.txn_id,
                "sk": f"L#{number:03d}",
                "record_type": "LEG",
                "txn_type": posting.txn_type,
                "status": "POSTED",
                "leg_no": number,
                "account": leg.account,
                "unit": leg.unit,
                "amount": leg.amount,
                "business_date": posting.business_date,
                "event_ts": posting.event_ts,
                "idempotency_key": posting.idempotency_key,
            }
        )

    def _balance_update(self, posting: Posting, leg: Leg, now: str) -> dict[str, Any]:
        update: dict[str, Any] = {
            "TableName": self.n.balances_table,
            "Key": _item({"account": leg.account, "unit": leg.unit}),
            "UpdateExpression": "ADD balance :amt, entry_count :one SET updated_at = :now, last_txn_id = :txn",
            "ExpressionAttributeValues": _item(
                {":amt": leg.amount, ":one": 1, ":now": now, ":txn": posting.txn_id}
            ),
        }
        floor = posting.constraints.get((leg.account, leg.unit))
        if floor is not None:
            expression, values = self._condition(floor, leg.amount)
            update["ConditionExpression"] = expression
            update["ExpressionAttributeValues"].update(_item(values))
        return {"Update": update}

    @staticmethod
    def _condition(floor: Floor, amount: Decimal) -> tuple[str, dict[str, Decimal]]:
        """Translate "balance after >= / <= X" into a condition on the balance *before*."""
        parts: list[str] = []
        values: dict[str, Decimal] = {}
        if floor.min_after is not None:
            need = floor.min_after - amount
            if need <= 0:
                parts.append("(attribute_not_exists(balance) OR balance >= :need)")
            else:
                parts.append("balance >= :need")
            values[":need"] = need
        if floor.max_after is not None:
            cap = floor.max_after - amount
            if cap >= 0:
                parts.append("(attribute_not_exists(balance) OR balance <= :cap)")
            else:
                parts.append("balance <= :cap")
            values[":cap"] = cap
        return " AND ".join(parts), values

    # ------------------------------------------------------------------- reads

    def balance(self, account: str, unit: str) -> Decimal:
        item = self._get(self.n.balances_table, {"account": account, "unit": unit})
        return Decimal(item.get("balance", 0)) if item else Decimal(0)

    def scan_balances(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        paginator = self.aws.ddb.get_paginator("scan")
        for page in paginator.paginate(TableName=self.n.balances_table, ConsistentRead=True):
            out.extend(_plain(i) for i in page["Items"])
        return out
