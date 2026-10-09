"""Emulator-only performance shim for moto's DynamoDB ``TransactWriteItems``.

moto 5.x backs up every table touched by a transaction with ``copy.deepcopy`` before applying
it, so each call costs O(table size) and a few thousand ledger postings take minutes. This shim
keeps DynamoDB's semantics -- every condition is evaluated against the pre-transaction state,
and either every write is applied or none is -- by checking all conditions first and applying
the writes only when all of them pass. It is active only inside in-process moto; LocalStack and
real AWS are untouched. ``tests/unit/test_moto_patches.py`` pins the behaviour.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any


def _fast_transact_write_items(self: Any, transact_items: list[dict[str, Any]]) -> None:
    from moto.dynamodb.comparisons import get_filter_expression
    from moto.dynamodb.exceptions import (
        MultipleTransactionsException,
        TooManyTransactionsException,
        TransactionCanceledException,
    )

    if len(transact_items) > 100:
        raise TooManyTransactionsException()

    seen: set[tuple[str, str]] = set()
    plan: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    reasons: list[tuple[str | None, str | None, Any]] = []
    for item in transact_items:
        op_type, op = next(iter(item.items()))
        if op_type not in {"ConditionCheck", "Put", "Delete", "Update"}:
            raise ValueError(f"Unsupported transaction operation {op_type}")
        table_name = op["TableName"]
        table = self.get_table(table_name)
        if op_type == "Put":
            attrs = op["Item"]
            key = {table.hash_key_attr: attrs[table.hash_key_attr]}
            if table.range_key_attr is not None:
                key[table.range_key_attr] = attrs[table.range_key_attr]
        else:
            key = op["Key"]
        marker = (table_name, str(dict(sorted(key.items()))))
        if marker in seen:
            raise MultipleTransactionsException()
        seen.add(marker)

        current = self.get_item(table_name, key)
        condition = op.get("ConditionExpression")
        passed = True
        if condition is not None:
            expr = get_filter_expression(
                condition,
                op.get("ExpressionAttributeNames"),
                op.get("ExpressionAttributeValues"),
            )
            passed = bool(expr.expr(current))
        if passed:
            reasons.append((None, None, None))
        else:
            old = None
            if op.get("ReturnValuesOnConditionCheckFailure") == "ALL_OLD" and current:
                old = current.to_json()["Attributes"]
            reasons.append(("ConditionalCheckFailed", "The conditional request failed", old))
        plan.append((op_type, op, key))

    if any(code is not None for code, _, _ in reasons):
        raise TransactionCanceledException(reasons)

    for op_type, op, key in plan:
        names = op.get("ExpressionAttributeNames")
        values = op.get("ExpressionAttributeValues")
        if op_type == "Put":
            self.put_item(
                op["TableName"],
                op["Item"],
                expression_attribute_names=names,
                expression_attribute_values=values,
            )
        elif op_type == "Delete":
            self.delete_item(op["TableName"], key)
        elif op_type == "Update":
            self.update_item(
                op["TableName"],
                key,
                update_expression=op["UpdateExpression"],
                expression_attribute_names=names or {},
                expression_attribute_values=values or {},
            )


@contextlib.contextmanager
def fast_transactions() -> Iterator[None]:
    from moto.dynamodb.models import DynamoDBBackend

    original = DynamoDBBackend.transact_write_items
    DynamoDBBackend.transact_write_items = _fast_transact_write_items  # type: ignore[method-assign]
    try:
        yield
    finally:
        DynamoDBBackend.transact_write_items = original  # type: ignore[method-assign]
