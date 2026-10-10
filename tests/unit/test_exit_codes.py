"""``fbc simulate`` exits 4 on a reconciliation break and 3 when the lake gate fails on a ledger
that reconciles. The lake gate lists the reconciliation among its checks, so a break must be
reported before the gate is applied."""

from __future__ import annotations

import json

from fbc.cli import main
from fbc.ledger import stream
from fbc.ledger.service import LedgerService


def _run_file(settings, name: str):
    return json.loads((settings.run_dir / name).read_text(encoding="utf-8"))


def test_simulate_exit_codes(fixture_settings, monkeypatch):
    data = ["--data-dir", str(fixture_settings.data_dir), "--trading-days", "4"]
    sim = [*data, "--customers", "3", "--cards", "5", "--seed", "3"]
    assert main(["ingest", "--offline", *data]) == 0

    # A balance the journal does not explain: the reconciliation breaks.
    scan = LedgerService.scan_balances

    def tampered(self):
        balances = scan(self)
        balances[0]["balance"] += 1
        return balances

    with monkeypatch.context() as m:
        m.setattr(LedgerService, "scan_balances", tampered)
        assert main(["simulate", *sim]) == 4
    summary = _run_file(fixture_settings, "simulation_summary.json")
    assert summary["reconciliation_ok"] is False
    assert "trial_balance" in {c["check"] for c in summary["reconciliation"] if not c["ok"]}

    # Business events that lost their simulated flag: the lake gate fails, the ledger reconciles.
    rows = stream.to_firehose_rows

    def unlabelled(record):
        mapped = rows(record)
        if mapped and mapped[0] == "events":
            mapped[1]["simulated"] = False
        return mapped

    with monkeypatch.context() as m:
        m.setattr(stream, "to_firehose_rows", unlabelled)
        assert main(["simulate", *sim]) == 3
    assert _run_file(fixture_settings, "simulation_summary.json")["reconciliation_ok"] is True
    failed = [c["name"] for c in _run_file(fixture_settings, "dq_lake.json") if not c["passed"]]
    assert failed == ["events_labelled_simulated"]
