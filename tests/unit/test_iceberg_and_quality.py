from datetime import date

import pyarrow as pa
import pytest

from fbc.lake import iceberg
from fbc.quality.checks import CheckResult, QualityGateFailed, gate

SCHEMA = pa.schema(
    [("k", pa.string()), ("business_date", pa.date32()), ("v", pa.int64()), ("seq", pa.string())]
)


def _t(rows):
    return pa.Table.from_pylist(rows, schema=SCHEMA)


def test_merge_partitions_is_idempotent(settings):
    cat = iceberg.get_catalog(settings)
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    batch = _t(
        [
            {"k": "a", "business_date": d1, "v": 1, "seq": "1"},
            {"k": "b", "business_date": d2, "v": 2, "seq": "2"},
        ]
    )
    assert (
        iceberg.merge_partitions(
            cat, "t", batch, partition_col="business_date", keys=["k"], order_col="seq"
        )
        == 2
    )
    for _ in range(3):
        iceberg.merge_partitions(
            cat, "t", batch, partition_col="business_date", keys=["k"], order_col="seq"
        )
    assert iceberg.read(cat, "t").num_rows == 2


def test_merge_keeps_latest_and_leaves_other_partitions(settings):
    cat = iceberg.get_catalog(settings)
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    iceberg.merge_partitions(
        cat,
        "t",
        _t(
            [
                {"k": "a", "business_date": d1, "v": 1, "seq": "1"},
                {"k": "b", "business_date": d2, "v": 2, "seq": "1"},
            ]
        ),
        partition_col="business_date",
        keys=["k"],
        order_col="seq",
    )
    iceberg.merge_partitions(
        cat,
        "t",
        _t([{"k": "a", "business_date": d1, "v": 9, "seq": "2"}]),
        partition_col="business_date",
        keys=["k"],
        order_col="seq",
    )
    rows = {r["k"]: r["v"] for r in iceberg.read(cat, "t").to_pylist()}
    assert rows == {"a": 9, "b": 2}


def test_replace_all_and_schema_evolution(settings):
    cat = iceberg.get_catalog(settings)
    iceberg.replace_all(cat, "r", pa.table({"x": [1, 2]}))
    iceberg.replace_all(cat, "r", pa.table({"x": [3], "y": ["new"]}))
    assert iceberg.read(cat, "r").to_pylist() == [{"x": 3, "y": "new"}]


def test_gate_fails_only_on_errors():
    gate([CheckResult("raw", "w", "warn", False, "x")])
    with pytest.raises(QualityGateFailed):
        gate([CheckResult("raw", "e", "error", False, "x")])
