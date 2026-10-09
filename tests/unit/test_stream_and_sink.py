import json

from fbc.lake import iceberg
from fbc.lake.sink import sink
from fbc.ledger.stream import StreamPoller, handler, to_firehose_rows
from fbc.recon import reconcile


def _post_some(api):
    base = {"business_date": "2026-10-01", "event_ts": "2026-10-01T10:00:00"}
    for i in range(30):
        api.handler(
            {
                "routeKey": "POST /deposits",
                "headers": {"Idempotency-Key": f"d{i}"},
                "body": json.dumps({**base, "customer_id": f"C{i % 5}", "amount": "100"}),
            }
        )
    # one rejection (no cash) so a REJECTED header goes through the stream too
    api.handler(
        {
            "routeKey": "POST /card-payments",
            "headers": {"Idempotency-Key": "p0"},
            "body": json.dumps({**base, "card_id": "K1", "amount": "5"}),
        }
    )


def _arn(aws):
    return aws.ddb.describe_table(TableName=aws.names.journal_table)["Table"]["LatestStreamArn"]


def test_stream_record_mapping():
    record = {
        "eventName": "INSERT",
        "dynamodb": {
            "SequenceNumber": "1",
            "NewImage": {
                "txn_id": {"S": "t"},
                "sk": {"S": "L#001"},
                "record_type": {"S": "LEG"},
                "txn_type": {"S": "DEPOSIT"},
                "status": {"S": "POSTED"},
                "business_date": {"S": "2026-10-01"},
                "event_ts": {"S": "2026-10-01T10:00:00"},
                "idempotency_key": {"S": "k"},
                "leg_no": {"N": "1"},
                "account": {"S": "CUST#C1:CASH"},
                "unit": {"S": "INR"},
                "amount": {"N": "100.00"},
            },
        },
    }
    kind, row = to_firehose_rows(record)
    assert kind == "ledger" and row["amount"] == "100.00" and row["simulated"] is True
    assert to_firehose_rows({**record, "eventName": "MODIFY"}) is None


def test_redelivered_batches_are_deduplicated_in_iceberg(aws, api, ledger, settings):
    _post_some(api)
    poller = StreamPoller(
        aws, _arn(aws), lambda e, c: handler(e, c, aws), batch_size=10, redeliver_rate=1.0
    )
    stats = poller.drain()
    assert stats["redelivered_records"] == stats["records"] > 0
    cat = iceberg.get_catalog(settings)
    out = sink(aws, cat)
    # every leg landed twice in S3, once in Iceberg
    assert out["ledger_entries_landed_rows"] == 2 * out["ledger_entries_rows_after_merge"] == 120
    assert out["business_events_rows_after_merge"] == 31
    # running the sink again changes nothing
    again = sink(aws, cat)
    assert again["ledger_entries_rows_after_merge"] == 60
    checks = reconcile(
        ledger.scan_balances(),
        iceberg.read(cat, "ledger_entries"),
        iceberg.read(cat, "business_events"),
        {},
    )
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_reconciliation_detects_a_lost_stream_record(aws, api, ledger, settings):
    _post_some(api)
    StreamPoller(aws, _arn(aws), lambda e, c: handler(e, c, aws)).drain()
    cat = iceberg.get_catalog(settings)
    sink(aws, cat)
    entries = iceberg.read(cat, "ledger_entries").slice(1)  # drop one leg
    checks = reconcile(ledger.scan_balances(), entries, iceberg.read(cat, "business_events"), {})
    broken = {c.name for c in checks if not c.ok}
    assert "stream_completeness" in broken


def test_partial_batch_failure_resumes_from_lowest_failed_sequence_numerically():
    """Sequence numbers are numeric strings; "9" must sort before "10"."""
    from fbc.ledger.stream import StreamPoller

    records = [{"dynamodb": {"SequenceNumber": s}} for s in ("9", "10", "11")]
    calls: list[list[str]] = []

    def fn(event, ctx):
        calls.append([r["dynamodb"]["SequenceNumber"] for r in event["Records"]])
        return {"batchItemFailures": [{"itemIdentifier": "10"}]} if len(calls) == 1 else {}

    poller = StreamPoller(None, "arn:stream", fn)  # type: ignore[arg-type]
    poller._invoke(records)
    assert calls == [["9", "10", "11"], ["10", "11"]]
    assert poller.stats["retried_records"] == 2
