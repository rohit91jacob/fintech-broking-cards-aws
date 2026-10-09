"""Create the emulated AWS resources. Mirrors infra/terraform (same names, keys, stream view)."""

from __future__ import annotations

import logging
from typing import Any

from fbc.aws.clients import Aws

log = logging.getLogger(__name__)

# Firehose buffers into time-partitioned prefixes; the Iceberg sink reads everything under these.
LEDGER_PREFIX = "ledger_entries/"
EVENTS_PREFIX = "business_events/"


def _table_exists(aws: Aws, name: str) -> bool:
    try:
        aws.ddb.describe_table(TableName=name)
    except aws.ddb.exceptions.ResourceNotFoundException:
        return False
    return True


def _create_table(aws: Aws, name: str, keys: list[tuple[str, str, str]], stream: bool) -> None:
    if _table_exists(aws, name):
        return
    spec: dict[str, Any] = {
        "TableName": name,
        "KeySchema": [{"AttributeName": n, "KeyType": k} for n, _, k in keys],
        "AttributeDefinitions": [{"AttributeName": n, "AttributeType": t} for n, t, _ in keys],
        "BillingMode": "PAY_PER_REQUEST",
        "SSESpecification": {"Enabled": True},
    }
    if stream:
        spec["StreamSpecification"] = {"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"}
    aws.ddb.create_table(**spec)
    aws.ddb.get_waiter("table_exists").wait(TableName=name)
    log.info("created table %s", name)


def ensure_resources(aws: Aws) -> dict[str, str]:
    n = aws.names
    _create_table(aws, n.journal_table, [("txn_id", "S", "HASH"), ("sk", "S", "RANGE")], True)
    _create_table(aws, n.balances_table, [("account", "S", "HASH"), ("unit", "S", "RANGE")], False)
    _create_table(aws, n.idempotency_table, [("idempotency_key", "S", "HASH")], False)

    existing = {b["Name"] for b in aws.s3.list_buckets().get("Buckets", [])}
    if n.landing_bucket not in existing:
        kwargs: dict[str, Any] = {"Bucket": n.landing_bucket}
        if aws.settings.aws_region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": aws.settings.aws_region}
        aws.s3.create_bucket(**kwargs)

    streams = set(aws.firehose.list_delivery_streams().get("DeliveryStreamNames", []))
    for stream_name, prefix in ((n.ledger_stream, LEDGER_PREFIX), (n.events_stream, EVENTS_PREFIX)):
        if stream_name in streams:
            continue
        aws.firehose.create_delivery_stream(
            DeliveryStreamName=stream_name,
            DeliveryStreamType="DirectPut",
            ExtendedS3DestinationConfiguration={
                # Emulators do not enforce IAM; the Terraform defines the least-privilege role.
                "RoleARN": "arn:aws:iam::000000000000:role/fbc-firehose-emulated",
                "BucketARN": f"arn:aws:s3:::{n.landing_bucket}",
                "Prefix": prefix,
                "ErrorOutputPrefix": "errors/",
                "BufferingHints": {"SizeInMBs": 1, "IntervalInSeconds": 60},
                "CompressionFormat": "UNCOMPRESSED",
            },
        )
    _wait_streams_active(aws, [n.ledger_stream, n.events_stream])
    stream_arn = aws.ddb.describe_table(TableName=n.journal_table)["Table"]["LatestStreamArn"]
    return {"journal_stream_arn": stream_arn}


def _wait_streams_active(aws: Aws, names: list[str]) -> None:
    import time

    for name in names:
        for _ in range(60):
            status = aws.firehose.describe_delivery_stream(DeliveryStreamName=name)[
                "DeliveryStreamDescription"
            ]["DeliveryStreamStatus"]
            if status == "ACTIVE":
                break
            time.sleep(1)
