"""DynamoDB Streams -> Lambda -> Kinesis Data Firehose.

``handler`` is the Lambda function: it receives a batch of journal stream records, flattens
HEADER items into *business events* and LEG items into *ledger entries*, and writes them to
two Firehose delivery streams with ``PutRecordBatch``. It returns a partial-batch response
(``batchItemFailures``) so only failed records are retried.

``StreamPoller`` stands in for the Lambda event source mapping: it reads every shard with
``GetRecords``, invokes the handler in batches, and -- to exercise at-least-once delivery --
re-delivers a seeded fraction of batches. Duplicates are removed downstream by the Iceberg
sink, which merges on (txn_id, leg_no).
"""

from __future__ import annotations

import json
import logging
import random
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from boto3.dynamodb.types import TypeDeserializer

from fbc.aws.clients import Aws

log = logging.getLogger(__name__)
_de = TypeDeserializer()

FIREHOSE_BATCH = 500


def _image(record: dict[str, Any]) -> dict[str, Any]:
    image = record["dynamodb"].get("NewImage") or {}
    return {k: _de.deserialize(v) for k, v in image.items()}


def to_firehose_rows(record: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Map one stream record to (kind, row). Only INSERTs are shipped: the journal is append-only."""
    if record.get("eventName") != "INSERT":
        return None
    item = _image(record)
    seq = record["dynamodb"].get("SequenceNumber")
    common = {
        "txn_id": item["txn_id"],
        "txn_type": item["txn_type"],
        "status": item["status"],
        "business_date": item["business_date"],
        "event_ts": item["event_ts"],
        "idempotency_key": item["idempotency_key"],
        "stream_sequence": seq,
        "simulated": True,
    }
    if item.get("record_type") == "LEG":
        return "ledger", {
            **common,
            "leg_no": int(item["leg_no"]),
            "account": item["account"],
            "unit": item["unit"],
            "amount": str(item["amount"]),
        }
    payload = json.loads(item.get("payload") or "{}")
    return "events", {**common, "reason": item.get("reason"), **payload}


def handler(event: dict[str, Any], context: Any, aws: Aws) -> dict[str, Any]:
    """Lambda entry point (the deployed wrapper binds ``aws`` from the environment)."""
    batches: dict[str, list[tuple[str, bytes]]] = {"ledger": [], "events": []}
    for record in event.get("Records", []):
        mapped = to_firehose_rows(record)
        if mapped is None:
            continue
        kind, row = mapped
        line = (json.dumps(row, separators=(",", ":"), default=str) + "\n").encode()
        batches[kind].append((record["dynamodb"]["SequenceNumber"], line))

    failed: set[str] = set()
    streams = {"ledger": aws.names.ledger_stream, "events": aws.names.events_stream}
    for kind, rows in batches.items():
        for start in range(0, len(rows), FIREHOSE_BATCH):
            chunk = rows[start : start + FIREHOSE_BATCH]
            resp = aws.firehose.put_record_batch(
                DeliveryStreamName=streams[kind], Records=[{"Data": data} for _, data in chunk]
            )
            if resp.get("FailedPutCount"):
                for (seq, _), result in zip(chunk, resp["RequestResponses"], strict=True):
                    if result.get("ErrorCode"):
                        failed.add(seq)
    return {"batchItemFailures": [{"itemIdentifier": seq} for seq in sorted(failed)]}


class StreamPoller:
    """Emulates the DynamoDB-stream event source mapping for one stream ARN."""

    def __init__(
        self,
        aws: Aws,
        stream_arn: str,
        fn: Callable[[dict[str, Any], Any], dict[str, Any]],
        *,
        batch_size: int = 100,
        redeliver_rate: float = 0.0,
        seed: int = 0,
    ) -> None:
        self.aws = aws
        self.stream_arn = stream_arn
        self.fn = fn
        self.batch_size = batch_size
        self.redeliver_rate = redeliver_rate
        self.rng = random.Random(seed)  # noqa: S311 - seeded fault injection, not security
        self.stats: Counter[str] = Counter()
        self.seen: set[str] = set()  # distinct sequence numbers delivered at least once
        self._positions: dict[str, str] = {}  # shard -> last processed sequence number

    def _shards(self) -> Iterable[str]:
        desc = self.aws.streams.describe_stream(StreamArn=self.stream_arn)["StreamDescription"]
        return [s["ShardId"] for s in desc["Shards"]]

    def _iterator(self, shard: str) -> str:
        kwargs: dict[str, Any] = {"StreamArn": self.stream_arn, "ShardId": shard}
        if shard in self._positions:
            kwargs.update(
                ShardIteratorType="AFTER_SEQUENCE_NUMBER",
                SequenceNumber=self._positions[shard],
            )
        else:
            kwargs["ShardIteratorType"] = "TRIM_HORIZON"
        return self.aws.streams.get_shard_iterator(**kwargs)["ShardIterator"]

    def drain(self) -> Counter[str]:
        """Process everything currently in the stream, then return the counters."""
        for shard in self._shards():
            iterator: str | None = self._iterator(shard)
            while iterator:
                resp = self.aws.streams.get_records(ShardIterator=iterator, Limit=self.batch_size)
                records = resp.get("Records", [])
                if not records:
                    break
                self._invoke(records)
                self.seen.update(r["dynamodb"]["SequenceNumber"] for r in records)
                self._positions[shard] = records[-1]["dynamodb"]["SequenceNumber"]
                iterator = resp.get("NextShardIterator")
        return self.stats

    def _invoke(self, records: list[dict[str, Any]], attempt: int = 0) -> None:
        event = {"Records": [{**r, "eventSourceARN": self.stream_arn} for r in records]}
        result = self.fn(event, None)
        self.stats["invocations"] += 1
        self.stats["records"] += len(records)
        failures = [f["itemIdentifier"] for f in result.get("batchItemFailures", [])]
        if failures:
            if attempt >= 3:
                raise RuntimeError(f"{len(failures)} records failed after retries")
            first = min(failures)
            retry = [r for r in records if r["dynamodb"]["SequenceNumber"] >= first]
            self.stats["retried_records"] += len(retry)
            self._invoke(retry, attempt + 1)
            return
        if self.redeliver_rate and self.rng.random() < self.redeliver_rate:
            # At-least-once: the same batch arrives again (e.g. after a Lambda timeout).
            self.stats["redelivered_records"] += len(records)
            self.fn(event, None)
