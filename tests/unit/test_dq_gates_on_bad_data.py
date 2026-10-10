"""The raw data-quality gate must stop the pipeline when the input is bad, not only when a
hand-built result list says so: corrupt a copy of the fixtures and run the real ingest."""

from __future__ import annotations

import dataclasses
import io
import json
import shutil
import zipfile

import pytest

from fbc.cli import main
from fbc.ingest import read_manifest
from fbc.pipeline import ingest
from fbc.quality.checks import QualityGateFailed
from tests.conftest import FIXTURES

NSE_FILE = "BhavCopy_NSE_CM_0_0_0_20260929_F_0000.csv.zip"


def _rewrite_first_eq_row(path, column: str, value: str) -> None:
    with zipfile.ZipFile(path) as zf:
        (name,) = zf.namelist()
        lines = zf.read(name).decode("utf-8").splitlines()
    header = lines[0].split(",")
    idx = header.index(column)
    for i, line in enumerate(lines[1:], start=1):
        cells = line.split(",")
        if cells[header.index("SctySrs")] == "EQ":
            cells[idx] = value
            lines[i] = ",".join(cells)
            break
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, "\n".join(lines) + "\n")
    path.write_bytes(buf.getvalue())


def test_raw_gate_fails_on_inconsistent_ohlc(fixture_settings):
    _rewrite_first_eq_row(fixture_settings.raw_dir / "nse" / NSE_FILE, "ClsPric", "999999.99")
    with pytest.raises(QualityGateFailed) as exc:
        ingest(fixture_settings, offline=True)
    assert [f.name for f in exc.value.failures] == ["nse_price_sanity"]


def test_raw_gate_passes_on_clean_fixtures(fixture_settings):
    result = ingest(fixture_settings, offline=True)
    assert result["dq"].get("failed_error", 0) == 0


# ---------------------------------------------------------------- NSE session completeness


def _check(settings, name: str) -> dict:
    report = json.loads((settings.run_dir / "dq_raw.json").read_text(encoding="utf-8"))
    return next(r for r in report if r["name"] == name)


def _redate(src, dst, old: str, new: str) -> None:
    """Copy a bhavcopy as another session's file (same rows, new trade date)."""
    with zipfile.ZipFile(src) as zf:
        (name,) = zf.namelist()
        text = zf.read(name).decode("utf-8").replace(old, new)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name.replace(old.replace("-", ""), new.replace("-", "")), text)


def test_full_nse_window_passes(fixture_settings):
    # conftest asks for 4 sessions; the fixtures hold 28 Sep - 1 Oct 2026.
    ingest(fixture_settings, offline=True)
    check = _check(fixture_settings, "nse_trading_days")
    assert check["passed"] and check["severity"] == "error"
    assert check["detail"] == (
        "4 of 4 expected sessions 2026-09-28..2026-10-01; missing none (tolerance 0)"
    )


def test_short_nse_window_fails_the_raw_gate(fixture_settings):
    short = dataclasses.replace(fixture_settings, trading_days=10)
    with pytest.raises(QualityGateFailed) as exc:
        ingest(short, offline=True)
    assert [f.name for f in exc.value.failures] == ["nse_trading_days"]
    assert exc.value.failures[0].severity == "error"
    assert exc.value.failures[0].detail.startswith(
        "4 of 10 expected sessions 2026-09-18..2026-10-01; missing ['2026-09-18', "
    )


def test_short_nse_window_exits_3(fixture_settings):
    args = ["--data-dir", str(fixture_settings.data_dir), "--trading-days"]
    assert main(["ingest", "--offline", *args, "10"]) == 3
    assert main(["ingest", "--offline", *args, "4"]) == 0


def test_missing_session_inside_the_window_fails_unless_tolerated(fixture_settings):
    (fixture_settings.raw_dir / "nse" / NSE_FILE).unlink()  # 29 Sep 2026
    three = dataclasses.replace(fixture_settings, trading_days=3)
    with pytest.raises(QualityGateFailed) as exc:
        ingest(three, offline=True)
    assert exc.value.failures[0].detail == (
        "2 of 3 expected sessions 2026-09-29..2026-10-01; missing ['2026-09-29'] (tolerance 0)"
    )
    tolerant = dataclasses.replace(three, nse_session_tolerance=1)
    assert ingest(tolerant, offline=True)["dq"].get("failed_error", 0) == 0


def test_exchange_holiday_is_not_a_missing_session(fixture_settings):
    # A 5 Oct 2026 session after Gandhi Jayanti (Fri 2 Oct) and the weekend.
    nse_dir = fixture_settings.raw_dir / "nse"
    _redate(
        nse_dir / "BhavCopy_NSE_CM_0_0_0_20261001_F_0000.csv.zip",
        nse_dir / "BhavCopy_NSE_CM_0_0_0_20261005_F_0000.csv.zip",
        "2026-10-01",
        "2026-10-05",
    )
    five = dataclasses.replace(fixture_settings, trading_days=5)
    ingest(five, offline=True)
    check = _check(five, "nse_trading_days")
    assert check["passed"], check["detail"]
    assert check["detail"].startswith("5 of 5 expected sessions 2026-09-28..2026-10-05")


def test_missing_nse_and_rbi_fall_back_to_the_synthetic_fixtures(settings):
    # A real run where NSE is off and the RBI refused: only SEC files in the raw directory.
    shutil.copytree(FIXTURES / "sec", settings.raw_dir / "sec")
    ten = dataclasses.replace(settings, trading_days=10)
    assert ingest(ten, offline=True)["dq"].get("failed_error", 0) == 0
    manifest = read_manifest(ten)
    assert manifest["nse_sessions_required"] == 4  # the fixtures' own complete window
    assert manifest["sources"]["nse"]["fallback"] and manifest["sources"]["nse"]["synthetic"]
    assert manifest["sources"]["rbi"]["fallback"] and manifest["sources"]["rbi"]["synthetic"]
    assert _check(ten, "nse_trading_days")["detail"].startswith("4 of 4 expected sessions")
    # `fbc dq` re-checks against the window the ingest asked for, not $FBC_TRADING_DAYS.
    assert main(["dq", "--data-dir", str(ten.data_dir)]) == 0
