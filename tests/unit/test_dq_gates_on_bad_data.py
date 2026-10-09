"""The raw data-quality gate must stop the pipeline when the input is bad, not only when a
hand-built result list says so: corrupt a copy of the fixtures and run the real ingest."""

from __future__ import annotations

import io
import zipfile

import pytest

from fbc.pipeline import ingest
from fbc.quality.checks import QualityGateFailed

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
