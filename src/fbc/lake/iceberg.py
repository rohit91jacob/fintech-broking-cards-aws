"""Apache Iceberg tables via pyiceberg.

Locally the catalog is a SQLite ``SqlCatalog`` and the warehouse a directory -- stand-ins for
the AWS Glue Data Catalog and an S3 bucket (see infra/terraform/modules/lake). The table
layout, partitioning and snapshot semantics are Iceberg's own, so the same tables would work
against Glue + S3 by swapping the catalog properties.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.expressions import AlwaysTrue, In
from pyiceberg.table import Table
from pyiceberg.transforms import IdentityTransform, MonthTransform

from fbc.config import Settings

log = logging.getLogger(__name__)

NAMESPACE = "lake"


def catalog_properties(settings: Settings) -> dict[str, str]:
    return {
        "type": "sql",
        "uri": f"sqlite:///{settings.catalog_db.as_posix()}",
        "warehouse": str(settings.warehouse_dir),
    }


def get_catalog(settings: Settings) -> Catalog:
    settings.warehouse_dir.mkdir(parents=True, exist_ok=True)
    props = catalog_properties(settings)
    props.pop("type")
    cat = SqlCatalog("fbc", **props)
    with contextlib.suppress(NamespaceAlreadyExistsError):
        cat.create_namespace(NAMESPACE)
    return cat


def _ensure_table(cat: Catalog, name: str, schema: pa.Schema, partition_by: str | None) -> Table:
    ident = f"{NAMESPACE}.{name}"
    try:
        table = cat.load_table(ident)
    except NoSuchTableError:
        table = cat.create_table(ident, schema=schema)
        if partition_by:
            transform = MonthTransform() if partition_by.endswith("_month") else IdentityTransform()
            with table.update_spec() as spec:
                spec.add_field(partition_by, transform, partition_by + "_part")
        return table
    # Additive schema evolution: new nullable columns are added, nothing is dropped.
    existing = {f.name for f in table.schema().fields}
    missing = [f for f in schema if f.name not in existing]
    if missing:
        with table.update_schema() as update:
            for f in missing:
                update.union_by_name(pa.schema([f]))
    return table


def replace_all(cat: Catalog, name: str, data: pa.Table, partition_by: str | None = None) -> int:
    """Full refresh of a (small) reference table, as one atomic Iceberg snapshot."""
    table = _ensure_table(cat, name, data.schema, partition_by)
    table.overwrite(data.cast(table.schema().as_arrow()), overwrite_filter=AlwaysTrue())
    return data.num_rows


def merge_partitions(
    cat: Catalog,
    name: str,
    data: pa.Table,
    *,
    partition_col: str,
    keys: Sequence[str],
    order_col: str | None = None,
) -> int:
    """Idempotent upsert: for every partition value in ``data``, union with what is already
    there, keep one row per ``keys`` (the latest by ``order_col``) and overwrite only those
    partitions. Replaying the same input any number of times leaves the table unchanged.
    Returns the number of rows in the touched partitions after the merge.
    """
    table = _ensure_table(cat, name, data.schema, partition_col)
    target = table.schema().as_arrow()
    data = data.cast(target)
    values = pc.unique(data[partition_col]).to_pylist()
    if not values:
        return 0
    existing = table.scan(row_filter=In(partition_col, values)).to_arrow().cast(target)
    combined = pa.concat_tables([existing, data]) if existing.num_rows else data
    merged = _dedupe(combined, keys, order_col)
    table.overwrite(merged, overwrite_filter=In(partition_col, values))
    return merged.num_rows


def _dedupe(data: pa.Table, keys: Sequence[str], order_col: str | None) -> pa.Table:
    if data.num_rows == 0:
        return data
    idx = pa.array(range(data.num_rows), pa.int64())
    work = data.append_column("__row", idx)
    sort = [(k, "ascending") for k in keys]
    if order_col:
        sort.append((order_col, "descending"))
    sort.append(("__row", "descending"))
    work = work.sort_by(sort)
    key_cols = [work[k] for k in keys]
    keep = [True] * work.num_rows
    prev: tuple[object, ...] | None = None
    columns = [c.to_pylist() for c in key_cols]
    for i in range(work.num_rows):
        current = tuple(col[i] for col in columns)
        keep[i] = current != prev
        prev = current
    return work.filter(pa.array(keep)).drop_columns(["__row"])


def read(cat: Catalog, name: str) -> pa.Table:
    return cat.load_table(f"{NAMESPACE}.{name}").scan().to_arrow()


def table_exists(cat: Catalog, name: str) -> bool:
    try:
        cat.load_table(f"{NAMESPACE}.{name}")
    except NoSuchTableError:
        return False
    return True


def drop(cat: Catalog, name: str) -> None:
    if table_exists(cat, name):
        cat.purge_table(f"{NAMESPACE}.{name}")


def snapshot_count(cat: Catalog, name: str) -> int:
    return len(cat.load_table(f"{NAMESPACE}.{name}").metadata.snapshots)


def warehouse_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
