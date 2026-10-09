"""Runtime settings, read from environment variables (prefix ``FBC_``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    # The Makefile does `-include .env` + `export`, which keeps the quotes of KEY="a b".
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


# SEC asks automated clients to declare who they are. Override with your own name and e-mail.
DEFAULT_SEC_USER_AGENT = "fintech-broking-cards-aws portfolio-project contact@example.com"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    sec_user_agent: str
    aws_region: str
    aws_endpoint_url: str | None
    resource_prefix: str
    seed: int
    trading_days: int
    customers: int
    cards: int
    rbi_months: int
    hood_filings: int
    offline: bool = False
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def warehouse_dir(self) -> Path:
        return self.data_dir / "warehouse"

    @property
    def catalog_db(self) -> Path:
        return self.data_dir / "catalog.db"

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "fbc.duckdb"

    @property
    def run_dir(self) -> Path:
        return self.data_dir / "runs"

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "data_dir": Path(_env("FBC_DATA_DIR", "data")).expanduser().resolve(),
            "sec_user_agent": _env("FBC_SEC_USER_AGENT", DEFAULT_SEC_USER_AGENT),
            "aws_region": _env("FBC_AWS_REGION", "ap-south-1"),
            "aws_endpoint_url": os.environ.get("FBC_AWS_ENDPOINT_URL") or None,
            "resource_prefix": _env("FBC_RESOURCE_PREFIX", "fbc-dev"),
            "seed": _env_int("FBC_SEED", 20261009),
            "trading_days": _env_int("FBC_TRADING_DAYS", 10),
            "customers": _env_int("FBC_CUSTOMERS", 400),
            "cards": _env_int("FBC_CARDS", 1000),
            "rbi_months": _env_int("FBC_RBI_MONTHS", 12),
            "hood_filings": _env_int("FBC_HOOD_FILINGS", 12),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        if isinstance(values["data_dir"], str):
            values["data_dir"] = Path(values["data_dir"]).expanduser().resolve()
        return cls(**values)  # type: ignore[arg-type]
