"""Runtime settings, read from environment variables (prefix ``FBC_``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PROJECT_URL = "https://github.com/rohit91jacob/fintech-broking-cards-aws"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Committed synthetic NSE/RBI files (and trimmed real SEC excerpts); see tests/fixtures/README.md.
DEFAULT_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "raw"

try:
    __version__ = version("fintech-broking-cards-aws")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    # The Makefile does `-include .env` + `export`, which keeps the quotes of KEY="a b".
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name, "1" if default else "0").lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name}={value!r}: expected 1/0, true/false, yes/no or on/off")


# Every request says what it is and where it comes from; the pipeline never poses as a browser.
DEFAULT_USER_AGENT = f"fintech-broking-cards-aws/{__version__} (+{PROJECT_URL})"
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
    user_agent: str = DEFAULT_USER_AGENT
    # NSE's terms prohibit automated collection, so its download is opt-in (see DATA_LICENSE.md).
    nse_download: bool = False
    # Missing NSE sessions tolerated by the raw gate within the requested window.
    nse_session_tolerance: int = 0
    # Where NSE/RBI files come from when a source has none in the raw directory.
    fixture_dir: Path = DEFAULT_FIXTURE_DIR
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
            "user_agent": _env("FBC_USER_AGENT", DEFAULT_USER_AGENT),
            "nse_download": _env_bool("FBC_NSE_DOWNLOAD", False),
            "nse_session_tolerance": _env_int("FBC_NSE_SESSION_TOLERANCE", 0),
            "fixture_dir": Path(_env("FBC_FIXTURE_DIR", str(DEFAULT_FIXTURE_DIR))),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        for key in ("data_dir", "fixture_dir"):
            values[key] = Path(values[key]).expanduser().resolve()  # type: ignore[arg-type]
        if values["trading_days"] < 1:  # type: ignore[operator]
            raise ValueError("FBC_TRADING_DAYS must be 1 or more")
        if values["nse_session_tolerance"] < 0:  # type: ignore[operator]
            raise ValueError("FBC_NSE_SESSION_TOLERANCE must be 0 or more")
        return cls(**values)  # type: ignore[arg-type]
