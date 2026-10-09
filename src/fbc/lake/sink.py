"""Firehose landing zone (S3, JSON lines) -> Iceberg tables, merged idempotently.

On AWS, Firehose can write straight to Iceberg tables registered in Glue. Emulators only
support the S3 destination, so Firehose lands JSON lines in the landing bucket and this job
plays the part of Firehose's Iceberg writer: it parses every object under the two prefixes
and merges the rows into ``lake.ledger_entries`` and ``lake.business_events``, partitioned by
``business_date`` and de-duplicated on their keys. Re-running it is a no-op.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pyarrow as pa
from pyiceberg.catalog import Catalog

from fbc.aws.clients import Aws
from fbc.aws.resources import EVENTS_PREFIX, LEDGER_PREFIX
from fbc.lake import iceberg

LEDGER_SCHEMA = pa.schema(
    [
        ("txn_id", pa.string()),
        ("leg_no", pa.int32()),
        ("txn_type", pa.string()),
        ("status", pa.string()),
        ("business_date", pa.date32()),
        ("event_ts", pa.timestamp("us")),
        ("account", pa.string()),
        ("unit", pa.string()),
        ("amount", pa.decimal128(20, 4)),
        ("idempotency_key", pa.string()),
        ("stream_sequence", pa.string()),
        ("simulated", pa.bool_()),
    ]
)

EVENTS_SCHEMA = pa.schema(
    [
        ("txn_id", pa.string()),
        ("txn_type", pa.string()),
        ("status", pa.string()),
        ("reason", pa.string()),
        ("business_date", pa.date32()),
        ("event_ts", pa.timestamp("us")),
        ("idempotency_key", pa.string()),
        ("customer_id", pa.string()),
        ("card_id", pa.string()),
        ("symbol", pa.string()),
        ("isin", pa.string()),
        ("side", pa.string()),
        ("qty", pa.decimal128(20, 4)),
        ("price", pa.decimal128(20, 4)),
        ("notional", pa.decimal128(20, 4)),
        ("fees", pa.decimal128(20, 4)),
        ("amount", pa.decimal128(20, 4)),
        ("settlement_date", pa.date32()),
        ("trade_txn_id", pa.string()),
        ("trade_date", pa.date32()),
        ("channel", pa.string()),
        ("merchant_category", pa.string()),
        ("mcc", pa.string()),
        ("merchant_city", pa.string()),
        ("injected_fraud", pa.bool_()),
        ("fraud_scenario", pa.string()),
        ("stream_sequence", pa.string()),
        ("simulated", pa.bool_()),
    ]
)


def _coerce(row: dict[str, Any], schema: pa.Schema) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in schema:
        value = row.get(f.name)
        if value is None or value == "":
            out[f.name] = None
        elif pa.types.is_decimal(f.type):
            out[f.name] = Decimal(str(value))
        elif pa.types.is_date(f.type):
            out[f.name] = date.fromisoformat(str(value)[:10])
        elif pa.types.is_timestamp(f.type):
            out[f.name] = datetime.fromisoformat(str(value)).replace(tzinfo=None)
        elif pa.types.is_integer(f.type):
            out[f.name] = int(value)
        elif pa.types.is_boolean(f.type):
            out[f.name] = bool(value)
        else:
            out[f.name] = str(value)
    return out


def _read_prefix(aws: Aws, prefix: str) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    objects = 0
    paginator = aws.s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=aws.names.landing_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = aws.s3.get_object(Bucket=aws.names.landing_bucket, Key=obj["Key"])["Body"].read()
            objects += 1
            rows.extend(json.loads(line) for line in body.decode().splitlines() if line.strip())
    return rows, objects


def wait_for_landing(aws: Aws, min_rows: int, timeout_s: float = 180) -> int:
    """Firehose delivery is asynchronous on LocalStack/AWS: wait until ``min_rows`` lines landed."""
    deadline = time.monotonic() + timeout_s
    landed = 0
    while time.monotonic() < deadline:
        landed = sum(len(_read_prefix(aws, p)[0]) for p in (LEDGER_PREFIX, EVENTS_PREFIX))
        if landed >= min_rows:
            return landed
        time.sleep(2)
    raise RuntimeError(f"only {landed} of {min_rows} Firehose records landed in {timeout_s}s")


def sink(aws: Aws, cat: Catalog) -> Counter[str]:
    stats: Counter[str] = Counter()
    for prefix, name, schema, keys in (
        (LEDGER_PREFIX, "ledger_entries", LEDGER_SCHEMA, ("txn_id", "leg_no")),
        (EVENTS_PREFIX, "business_events", EVENTS_SCHEMA, ("txn_id",)),
    ):
        rows, objects = _read_prefix(aws, prefix)
        stats[f"{name}_objects"] = objects
        stats[f"{name}_landed_rows"] = len(rows)
        if not rows:
            continue
        table = pa.Table.from_pylist([_coerce(r, schema) for r in rows], schema=schema)
        stats[f"{name}_rows_after_merge"] = iceberg.merge_partitions(
            cat, name, table, partition_col="business_date", keys=keys, order_col="stream_sequence"
        )
    return stats
