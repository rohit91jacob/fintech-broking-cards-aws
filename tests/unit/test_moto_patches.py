"""The emulator shim must keep DynamoDB's all-or-nothing transaction semantics."""

import pytest
from botocore.exceptions import ClientError

from fbc.aws.clients import Aws


def _tx(aws: Aws, items):
    return aws.ddb.transact_write_items(TransactItems=items)


def _put(aws: Aws, key: str, cond: bool = True):
    item = {
        "TableName": aws.names.idempotency_table,
        "Item": {"idempotency_key": {"S": key}},
    }
    if cond:
        item["ConditionExpression"] = "attribute_not_exists(idempotency_key)"
    return {"Put": item}


def _add(aws: Aws, account: str, amount: int, cond: str | None = None, values=None):
    upd = {
        "TableName": aws.names.balances_table,
        "Key": {"account": {"S": account}, "unit": {"S": "INR"}},
        "UpdateExpression": "ADD balance :a",
        "ExpressionAttributeValues": {":a": {"N": str(amount)}, **(values or {})},
    }
    if cond:
        upd["ConditionExpression"] = cond
    return {"Update": upd}


def _balance(aws: Aws, account: str):
    item = aws.ddb.get_item(
        TableName=aws.names.balances_table, Key={"account": {"S": account}, "unit": {"S": "INR"}}
    ).get("Item")
    return None if item is None else int(item["balance"]["N"])


def test_successful_transaction_applies_every_write(aws):
    _tx(aws, [_put(aws, "k1"), _add(aws, "A", 5), _add(aws, "B", -5)])
    assert _balance(aws, "A") == 5
    assert _balance(aws, "B") == -5


def test_failed_condition_cancels_everything(aws):
    _tx(aws, [_put(aws, "k1"), _add(aws, "A", 5)])
    with pytest.raises(ClientError) as err:
        # the second op fails: A has 5, needs >= 10
        _tx(
            aws,
            [
                _put(aws, "k2"),
                _add(aws, "A", -10, "balance >= :need", {":need": {"N": "10"}}),
                _add(aws, "B", 10),
            ],
        )
    assert err.value.response["Error"]["Code"] == "TransactionCanceledException"
    assert _balance(aws, "A") == 5
    assert _balance(aws, "B") is None
    assert "Item" not in aws.ddb.get_item(
        TableName=aws.names.idempotency_table, Key={"idempotency_key": {"S": "k2"}}
    )


def test_duplicate_key_is_reported_as_first_reason(aws):
    _tx(aws, [_put(aws, "k1"), _add(aws, "A", 1)])
    with pytest.raises(aws.ddb.exceptions.TransactionCanceledException) as err:
        _tx(aws, [_put(aws, "k1"), _add(aws, "A", 1)])
    assert "ConditionalCheckFailed" in str(err.value)
    assert _balance(aws, "A") == 1


def test_same_item_twice_is_a_validation_error(aws):
    with pytest.raises(ClientError):
        _tx(aws, [_add(aws, "A", 1), _add(aws, "A", 2)])
