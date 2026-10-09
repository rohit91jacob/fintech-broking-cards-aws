"""boto3 clients that point at in-process moto, an emulator endpoint (LocalStack/moto_server) or AWS."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config

from fbc.config import Settings


@dataclass(frozen=True)
class Names:
    """Resource names. They match the Terraform in infra/ for the same prefix."""

    prefix: str

    @property
    def journal_table(self) -> str:
        return f"{self.prefix}-ledger-journal"

    @property
    def balances_table(self) -> str:
        return f"{self.prefix}-ledger-balances"

    @property
    def idempotency_table(self) -> str:
        return f"{self.prefix}-ledger-idempotency"

    @property
    def landing_bucket(self) -> str:
        return f"{self.prefix}-firehose-landing"

    @property
    def ledger_stream(self) -> str:
        return f"{self.prefix}-ledger-entries"

    @property
    def events_stream(self) -> str:
        return f"{self.prefix}-business-events"


class Aws:
    """Lazily created, cached clients for one endpoint and region."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.names = Names(settings.resource_prefix)
        self._clients: dict[str, Any] = {}
        self._config = Config(retries={"max_attempts": 5, "mode": "standard"})

    def client(self, service: str) -> Any:
        if service not in self._clients:
            kwargs: dict[str, Any] = {
                "region_name": self.settings.aws_region,
                "config": self._config,
            }
            if self.settings.aws_endpoint_url:
                kwargs["endpoint_url"] = self.settings.aws_endpoint_url
            self._clients[service] = boto3.client(service, **kwargs)
        return self._clients[service]

    @property
    def ddb(self) -> Any:
        return self.client("dynamodb")

    @property
    def streams(self) -> Any:
        return self.client("dynamodbstreams")

    @property
    def s3(self) -> Any:
        return self.client("s3")

    @property
    def firehose(self) -> Any:
        return self.client("firehose")


@contextlib.contextmanager
def aws_backend(settings: Settings) -> Iterator[str]:
    """Yield the backend kind. Without an endpoint URL everything runs on in-process moto.

    With ``FBC_AWS_ENDPOINT_URL`` set (LocalStack in CI, or ``moto_server`` locally) the same
    boto3 calls go over HTTP to that endpoint. Real AWS is never used by this project.
    """
    if settings.aws_endpoint_url:
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
        yield "endpoint"
        return

    from moto import mock_aws

    from fbc.aws.moto_patches import fast_transactions

    # Placeholder credentials so botocore never looks for real ones.
    keys = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # noqa: S105 - moto placeholder
    try:
        with mock_aws(), fast_transactions():
            yield "moto"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
