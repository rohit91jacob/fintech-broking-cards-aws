"""The same pipeline against an AWS *endpoint* (HTTP), with a persistent backend.

In CI this runs against LocalStack (``FBC_AWS_ENDPOINT_URL=http://localhost:4566``). Locally,
without that variable, it starts moto's HTTP server in-process as a stand-in. The second run
replays every request against the state left by the first: the idempotency table must turn
all of them into replays, nothing may be posted twice, and the ledger must still reconcile.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator

import pytest

from fbc.config import Settings
from fbc.lake import iceberg
from fbc.pipeline import ingest, simulate
from tests.conftest import FIXTURES

pytestmark = pytest.mark.integration


@pytest.fixture
def endpoint() -> Iterator[str]:
    url = os.environ.get("FBC_AWS_ENDPOINT_URL")
    if url:
        yield url
        return
    from moto.server import ThreadedMotoServer

    from fbc.aws.moto_patches import fast_transactions

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    _, port = server.get_host_and_port()
    with fast_transactions():
        yield f"http://127.0.0.1:{port}"
    server.stop()


def test_pipeline_against_endpoint_is_idempotent_on_rerun(tmp_path, endpoint, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    s = Settings.from_env(
        data_dir=tmp_path / "data",
        aws_endpoint_url=endpoint,
        resource_prefix=f"fbc-it-{uuid.uuid4().hex[:6]}",
        trading_days=4,
        customers=12,
        cards=30,
        seed=11,
    )
    shutil.copytree(FIXTURES, s.raw_dir)
    ingest(s, offline=True)

    first = simulate(s)
    assert first["backend"] == "endpoint"
    assert first["reconciliation_ok"], first["reconciliation"]
    assert first["ledger"]["posted"] > 0

    cat = iceberg.get_catalog(s)
    rows = {t: iceberg.read(cat, t).num_rows for t in ("ledger_entries", "business_events")}

    second = simulate(s)
    assert second["reconciliation_ok"], second["reconciliation"]
    assert second["ledger"].get("posted", 0) == 0
    assert second["ledger"]["replayed"] >= first["ledger"]["posted"] + first["ledger"].get(
        "rejected", 0
    )
    assert {t: iceberg.read(cat, t).num_rows for t in rows} == rows
