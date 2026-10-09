"""Entry points for the deployed Lambda functions (see infra/terraform/modules/compute).

Locally the same code runs in-process; these wrappers only bind configuration from the
environment variables Terraform sets.
"""

from __future__ import annotations

import os
from typing import Any

from fbc.aws.clients import Aws
from fbc.config import Settings
from fbc.ledger.api import LedgerApi
from fbc.ledger.service import LedgerService
from fbc.ledger.stream import handler as _stream_handler

_aws: Aws | None = None


def _get_aws() -> Aws:
    global _aws
    if _aws is None:
        _aws = Aws(Settings.from_env(resource_prefix=os.environ.get("FBC_RESOURCE_PREFIX")))
    return _aws


def stream_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return _stream_handler(event, context, _get_aws())


def api_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    # Card limits would come from the card master table; the API refuses unknown cards.
    return LedgerApi(LedgerService(_get_aws())).handler(event, context)
