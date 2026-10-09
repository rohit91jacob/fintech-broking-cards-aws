# Runbook

## Exit codes

| Code | Meaning | First thing to check |
|---|---|---|
| 0 | OK | |
| 2 | A source file is missing (no SEC companyfacts, no NSE bhavcopy, no RBI workbook) | The `fetch` block printed by `fbc ingest`; `$FBC_DATA_DIR/raw/` |
| 3 | A data-quality gate failed (error severity) | `$FBC_DATA_DIR/runs/dq_raw.json` or `dq_lake.json` |
| 4 | Reconciliation break | `fbc reconcile`; `runs/simulation_summary.json` -> `reconciliation` |
| 5 | `dbt build` failed | `dbt/logs/dbt.log`, the failing model or test in the output |

## Everyday commands

```bash
uv run fbc ingest            # download (cached where immutable) + raw Iceberg + raw DQ gate
uv run fbc simulate          # simulated activity through the emulated AWS chain + recon + lake DQ
uv run fbc transform         # dbt build
uv run fbc report --out site # results site
uv run fbc dq                # re-run raw + lake checks against the current lake
uv run fbc reconcile         # latest reconciliation table, exit 4 on any BREAK
uv run fbc dbt -- test -s mart_ledger_reconciliation   # any dbt command with the right env
```

## Scheduled refresh fails

The `refresh` workflow opens (or comments on) an issue titled "Scheduled refresh is failing".

1. Open the failed run. The step name tells the layer: *Ingest*, *Simulate*, *dbt build*, *site*.
2. **Ingest, exit 2 / HTTP errors.**
   * **SEC 403** "Undeclared Automated Tool": SEC rejected the User-Agent. Set the repository
     variable `SEC_USER_AGENT` to `"<your name or project> <your e-mail>"`.
   * **NSE 403 / timeouts**: NSE throttles some cloud IP ranges. Past bhavcopies are reused
     from the Actions cache, so the job only fails if *no* recent file can be found. Re-run
     later; if it persists, run `fbc ingest` locally and inspect `raw/nse/`.
   * **RBI**: `rbidocs.rbi.org.in` sometimes serves a JavaScript challenge page instead of
     the workbook. `fbc.http.fetch` rejects any payload that is not a ZIP (`PK` magic) and
     retries; cached releases are reused.
3. **Ingest, exit 3.** Read `dq_raw.json` in the run log. Typical causes:
   * `rbi_cards_banks_sum_to_total` / `LayoutError`: the RBI changed a workbook layout. Compare
     the new file with the column map in `fbc/sources/rbi.py` (`CARD_COLUMNS`, numbered header
     row) and update the parser and the synthetic fixture generator together.
   * `hood_revenue_parts_sum_to_total`: a new 10-Q uses a different XBRL member for a revenue
     type. Add it to `REVENUE_MEMBERS_ALT` in `fbc/sources/sec.py`.
   * `hood_kpis_rows`: the KPI table label changed (it went from "AUC" to "Total Platform
     Assets" in 2025). Extend `_KPI_LABELS`.
4. **Simulate, exit 4.** See "Reconciliation break" below.
5. **dbt build.** Run `uv run fbc dbt -- build -s <model>+` locally against the same data.

Close the issue once a run is green.

## Reconciliation break

`fbc reconcile` lists every check. What each one means when it breaks:

| Check | A break means | Where to look |
|---|---|---|
| `trial_balance` | A posting was written unbalanced. `Posting.validate()` should make this impossible. | The unit in `scope`; `select txn_id, sum(amount) from lake.ledger_entries where unit = ? group by 1 having sum(amount) <> 0` |
| `stream_completeness` | DynamoDB balances differ from the sum of legs that reached Iceberg: records lost or double counted between the stream and the lake | `runs/simulation_summary.json` -> `stream`, `sink`; Firehose landing objects |
| `positions` / `customer_cash` | Ledger balances differ from what the order events imply | Compare `fct_positions_mtm` for the customer with `ledger_balances_snapshot` |
| `ccp_obligations` | Unsettled trades do not match the clearing accounts: a settlement was skipped or doubled | `mart_settlement_cash`, `int_trade_fills where not is_settled` |
| `card_outstanding` | Card balances differ from approved authorisations minus payments | `business_events where card_id = ?` |
| `constraints` | A negative customer balance or a card over its limit got through | The conditional expressions in `LedgerService._condition` |

The in-process emulator rebuilds the simulated tables on every run, so re-running
`fbc simulate` after a code fix starts clean. Against a persistent endpoint (LocalStack), the
idempotency table turns re-sent requests into replays and the Iceberg sink merges on
`(txn_id, leg_no)` / `txn_id`, so re-running is safe there too.

## Rebuild from scratch

```bash
rm -rf "$FBC_DATA_DIR"/{warehouse,catalog.db,fbc.duckdb,runs}   # keeps raw downloads
uv run fbc run --offline --report site
```

## Changing the simulation

`FBC_SEED`, `FBC_TRADING_DAYS`, `FBC_CUSTOMERS` and `FBC_CARDS` (see `.env.example`). The default
size (10 sessions, 400 customers, 1,000 cards, about 7,800 postings) takes 5-10 minutes on a
laptop; most of it is moto's per-request overhead. Fraud thresholds are dbt vars.

## Credentials

None are needed. No workflow uses static cloud keys. The optional `deploy` workflow assumes
an AWS role through GitHub OIDC and only runs when the repository variables
`AWS_DEPLOY_ROLE_ARN` and `TF_STATE_BUCKET` are set; it plans by default and applies only when
dispatched with `apply = true`.
