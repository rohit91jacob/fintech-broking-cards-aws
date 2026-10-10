import json
from decimal import Decimal

from fbc.ledger.api import brokerage
from fbc.ledger.stream import StreamPoller


def call(api, route, body, key="k"):
    resp = api.handler(
        {"routeKey": route, "headers": {"Idempotency-Key": key}, "body": json.dumps(body)}
    )
    return resp["statusCode"], json.loads(resp["body"])


BASE = {"business_date": "2026-10-01", "event_ts": "2026-10-01T09:30:00"}


def deposit(api, amount="10000", key="dep1", cid="C1"):
    return call(api, "POST /deposits", {**BASE, "customer_id": cid, "amount": amount}, key)


def order(api, side, qty, price="100", key="o1", cid="C1"):
    body = {
        **BASE,
        "customer_id": cid,
        "symbol": "SYN01",
        "isin": "INSYN000010",
        "side": side,
        "qty": qty,
        "price": price,
        "settlement_date": "2026-10-03",
    }
    return call(api, "POST /orders", body, key)


def test_deposit_then_replay_is_idempotent(api, ledger):
    assert deposit(api)[0] == 201
    code, body = deposit(api)
    assert code == 200 and body["status"] == "REPLAYED" and body["original_status"] == "POSTED"
    assert ledger.balance("CUST#C1:CASH", "INR") == Decimal("10000")
    assert ledger.balance("EXT:UPI_PAYIN", "INR") == Decimal("-10000")


def test_same_key_different_body_is_a_conflict(api, ledger):
    deposit(api)
    code, body = deposit(api, amount="99")
    assert code == 409 and body["status"] == "CONFLICT"
    assert ledger.balance("CUST#C1:CASH", "INR") == Decimal("10000")


def expire(ledger, key):
    """What DynamoDB TTL does to an idempotency record 30 days after it was written."""
    ledger.aws.ddb.delete_item(
        TableName=ledger.n.idempotency_table, Key={"idempotency_key": {"S": key}}
    )


def ledger_state(ledger):
    """Balances, journal items and every write the journal has seen (its stream), so that an
    overwrite with identical content shows up too."""
    events: list[str] = []

    def collect(event, _ctx):
        events.extend(r["eventName"] for r in event["Records"])
        return {}

    ddb = ledger.aws.ddb
    arn = ddb.describe_table(TableName=ledger.n.journal_table)["Table"]["LatestStreamArn"]
    StreamPoller(ledger.aws, arn, collect).drain()
    journal = ddb.scan(TableName=ledger.n.journal_table, ConsistentRead=True)["Items"]
    return (
        sorted(ledger.scan_balances(), key=lambda b: (b["account"], b["unit"])),
        sorted(journal, key=lambda i: (i["txn_id"]["S"], i["sk"]["S"])),
        events,
    )


def test_retry_after_the_idempotency_record_expired_does_not_post_twice(api, ledger):
    assert deposit(api)[0] == 201
    before = ledger_state(ledger)
    expire(ledger, "dep1")
    code, body = deposit(api)
    assert code == 200 and body["status"] == "REPLAYED" and body["original_status"] == "POSTED"
    # The key reused with a different body is still a conflict.
    code, body = deposit(api, amount="99")
    assert code == 409 and body["status"] == "CONFLICT"
    assert ledger_state(ledger) == before
    assert ledger.balance("CUST#C1:CASH", "INR") == Decimal("10000")


def test_rejection_retried_after_the_idempotency_record_expired_stays_rejected(api, ledger):
    deposit(api, amount="100")
    assert order(api, "BUY", 10, key="o1")[0] == 422  # 1,000 + fee > 100
    expire(ledger, "o1")
    deposit(api, amount="5000", key="dep2")  # enough cash now: a new posting would succeed
    before = ledger_state(ledger)
    code, body = order(api, "BUY", 10, key="o1")
    assert code == 200 and body["status"] == "REPLAYED" and body["original_status"] == "REJECTED"
    assert ledger_state(ledger) == before
    assert ledger.balance("CUST#C1:SEC_PENDING", "INSYN000010") == Decimal(0)


def test_buy_blocks_cash_and_overdraft_is_rejected(api, ledger):
    deposit(api)
    code, _ = order(api, "BUY", 50, key="o1")  # 5,000 + fee 1.50
    assert code == 201
    assert ledger.balance("CUST#C1:CASH", "INR") == Decimal("10000") - Decimal("5000") - Decimal(
        "1.50"
    )
    assert ledger.balance("CUST#C1:SEC_PENDING", "INSYN000010") == Decimal(50)
    code, body = order(api, "BUY", 100, key="o2")  # 10,000 > remaining cash
    assert code == 422 and "insufficient" in body["reason"]
    # retrying the rejected request returns the same rejection
    code, body = order(api, "BUY", 100, key="o2")
    assert code == 200 and body["original_status"] == "REJECTED"


def test_cannot_sell_unsettled_or_more_than_held(api, ledger):
    deposit(api)
    order(api, "BUY", 10, key="o1")
    code, _ = order(api, "SELL", 5, key="s1")  # shares still pending settlement
    assert code == 422
    settle = {
        **BASE,
        "trade_txn_id": "t1",
        "customer_id": "C1",
        "isin": "INSYN000010",
        "side": "BUY",
        "qty": 10,
        "notional": "1000",
        "fees": "0.30",
    }
    assert call(api, "POST /settlements", settle, "stl1")[0] == 201
    assert ledger.balance("CUST#C1:SEC", "INSYN000010") == Decimal(10)
    assert ledger.balance("CCP:CASH_PAYABLE", "INR") == Decimal(0)
    assert order(api, "SELL", 11, key="s2")[0] == 422
    assert order(api, "SELL", 10, key="s3")[0] == 201
    assert ledger.balance("CUST#C1:CASH_PENDING", "INR") == Decimal("1000") - Decimal("0.30")


def test_card_limit_and_bill_payment(api, ledger):
    auth = {**BASE, "card_id": "K1", "amount": "9000", "mcc": "5411"}
    assert call(api, "POST /card-authorizations", auth, "a1")[0] == 201
    assert call(api, "POST /card-authorizations", {**auth, "amount": "1500"}, "a2")[0] == 422
    assert call(api, "POST /card-authorizations", {**auth, "amount": "1000"}, "a3")[0] == 201
    assert ledger.balance("CARD#K1:OUTSTANDING", "INR") == Decimal("10000")
    pay = {**BASE, "card_id": "K1", "amount": "10000"}
    assert call(api, "POST /card-payments", pay, "p1")[0] == 201
    assert call(api, "POST /card-payments", pay, "p2")[0] == 422  # would go negative
    assert ledger.balance("CARD#K1:OUTSTANDING", "INR") == Decimal(0)


def test_bad_requests(api):
    assert call(api, "POST /deposits", {**BASE, "customer_id": "C1"})[0] == 400
    assert call(api, "POST /deposits", {**BASE, "customer_id": "C1", "amount": "-5"})[0] == 400
    assert order(api, "HOLD", 1)[0] == 400
    assert order(api, "BUY", 1.5)[0] == 400
    assert (
        call(api, "POST /card-authorizations", {**BASE, "card_id": "NOPE", "amount": "1"})[0] == 400
    )
    assert (
        api.handler({"routeKey": "POST /deposits", "headers": {}, "body": "{}"})["statusCode"]
        == 400
    )
    assert api.handler({"routeKey": "DELETE /x"})["statusCode"] == 404


def test_balance_route(api):
    deposit(api)
    resp = api.handler(
        {
            "routeKey": "GET /balances/{account}/{unit}",
            "pathParameters": {"account": "CUST#C1:CASH", "unit": "INR"},
        }
    )
    assert json.loads(resp["body"])["balance"] == "10000.00"


def test_brokerage_is_capped():
    assert brokerage(Decimal("10000")) == Decimal("3.00")
    assert brokerage(Decimal("1000000")) == Decimal("20.00")


def test_transaction_conflict_is_retryable_not_a_rejection(api, ledger, monkeypatch):
    """A TransactionConflict/throttle on real DynamoDB must not be stored as a rejection."""
    ddb = ledger.aws.ddb
    real = ddb.transact_write_items
    error = ddb.exceptions.TransactionCanceledException(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
            "CancellationReasons": [{"Code": "None"}, {"Code": "TransactionConflict"}],
        },
        "TransactWriteItems",
    )
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise error
        return real(**kwargs)

    monkeypatch.setattr(ddb, "transact_write_items", flaky)
    code, body = deposit(api)
    assert code == 503 and body["retryable"] is True
    assert ledger.balance("CUST#C1:CASH", "INR") == Decimal(0)
    # The retry with the same key is processed normally, not replayed as a rejection.
    code, body = deposit(api)
    assert code == 201 and body["status"] == "POSTED"
    assert ledger.balance("CUST#C1:CASH", "INR") == Decimal("10000")
