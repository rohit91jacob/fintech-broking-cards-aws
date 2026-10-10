from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from fbc.aws.clients import Aws, aws_backend
from fbc.aws.resources import ensure_resources
from fbc.config import Settings
from fbc.ledger.api import LedgerApi
from fbc.ledger.service import LedgerService

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in (
        "FBC_AWS_ENDPOINT_URL",
        "FBC_USER_AGENT",
        "FBC_NSE_DOWNLOAD",
        "FBC_NSE_SESSION_TOLERANCE",
        "FBC_FIXTURE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    return Settings.from_env(
        data_dir=tmp_path / "data",
        trading_days=4,
        customers=30,
        cards=80,
        seed=7,
        resource_prefix="fbc-test",
    )


@pytest.fixture
def fixture_settings(settings: Settings) -> Settings:
    """Settings whose raw directory holds a copy of tests/fixtures/raw."""
    shutil.copytree(FIXTURES, settings.raw_dir)
    return settings


@pytest.fixture
def aws(settings: Settings) -> Iterator[Aws]:
    with aws_backend(settings):
        client = Aws(settings)
        ensure_resources(client)
        yield client


@pytest.fixture
def ledger(aws: Aws) -> LedgerService:
    return LedgerService(aws)


@pytest.fixture
def api(ledger: LedgerService) -> LedgerApi:
    from decimal import Decimal

    return LedgerApi(ledger, card_limits={"K1": Decimal("10000")})
