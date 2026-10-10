# Runbook

## Exit codes

| Code | Meaning | First thing to check |
|---|---|---|
| 0 | OK | |
| 2 | A source file is missing: no SEC companyfacts, or no NSE/RBI files in the raw directory *and* none in `FBC_FIXTURE_DIR` | The `fetch` block printed by `fbc ingest`; `$FBC_DATA_DIR/raw/` |
| 3 | A data-quality gate failed (error severity): the raw gate in `fbc ingest` (e.g. `nse_trading_days`: sessions missing from the requested NSE window), the lake gate in `fbc simulate` on a ledger that reconciled, or either gate in `fbc dq` | `$FBC_DATA_DIR/runs/dq_raw.json` or `dq_lake.json` |
| 4 | Reconciliation break: `fbc simulate` and `fbc run` stop on it before the lake gate; `fbc reconcile` exits 4 while the latest reconciliation has a `BREAK` | `fbc reconcile`; `runs/simulation_summary.json` -> `reconciliation` |
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
   * **NSE "download is off"** (warning, not an error): expected. NSE's Terms of Use prohibit
     automated collection and the use of its data for simulation (DATA_LICENSE.md), so
     `FBC_NSE_DOWNLOAD` defaults to 0 and the committed synthetic bhavcopies are used. Do not
     switch it on without NSE's written permission. With it on, `nsearchives.nseindia.com`
     does not answer non-browser clients: the first timeout raises `Refused` and NSE is
     skipped (`"refused"` in the `fetch` block).
   * **RBI `"refused"`** (warning, not an error): `rbidocs.rbi.org.in` answers automated
     clients with a JavaScript challenge page instead of the workbook. `fbc.http.fetch` sees a
     challenge page where a ZIP (`PK`) was expected, retries, then raises `Refused`; ingest
     uses the workbooks already in `raw/rbi/` (none on a fresh runner) or the synthetic ones,
     and the site marks the UPI and card figures as synthetic. Do not change the User-Agent
     to a browser string to get past it.
3. **Ingest, exit 3.** Read `dq_raw.json` in the run log. Typical causes:
   * `nse_trading_days`: the NSE window does not hold every session the calendar expects
     (`missing [...]` in the detail). The window is the requested `FBC_TRADING_DAYS` sessions
     ending at the latest bhavcopy loaded; when NSE falls back to the committed fixtures it is
     capped at the fixtures' own complete 4-session window. Causes, in order of likelihood:
     a partial download (fewer files than requested), a failed day in the middle, or a day
     NSE was closed that `fbc/sources/nse_calendar.py` does not list (an election or other
     holiday declared at short notice). For the last one, add the date with NSE's description
     from its holiday circular and re-run. `FBC_NSE_SESSION_TOLERANCE` (default 0) lets a run
     pass with that many sessions missing; leave it at 0 for scheduled runs.
   * `nse_holiday_calendar_covers_window` (warn): the window reaches a year with no holiday
     list. Add NSE's list for that year (published in December) to `nse_calendar.py`.
   * `rbi_cards_banks_sum_to_total` / `LayoutError`: the RBI changed a workbook layout. Compare
     the new file with the column map in `fbc/sources/rbi.py` (`CARD_COLUMNS`, numbered header
     row) and update the parser and the synthetic fixture generator together.
   * `hood_revenue_parts_sum_to_total`: a new 10-Q uses a different XBRL member for a revenue
     type. Add it to `REVENUE_MEMBERS_ALT` in `fbc/sources/sec.py`.
   * `hood_kpis_rows`: the KPI table label changed (it went from "AUC" to "Total Platform
     Assets" in 2025). Extend `_KPI_LABELS`.
4. **Simulate, exit 4.** See "Reconciliation break" below. **Exit 3** is the lake gate failing
   on a ledger that reconciled; `dq_lake.json` names the check.
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

`FBC_SEED`, `FBC_TRADING_DAYS`, `FBC_CUSTOMERS` and `FBC_CARDS` (see `.env.example`). When NSE
falls back to the committed fixtures, the window is capped at their 4 sessions; when `raw/nse/`
holds bhavcopies, the raw gate requires all `FBC_TRADING_DAYS` sessions. The default size (400
customers, 1,000 cards, about 2,500 postings over the fixtures' 4 sessions) takes a couple of
minutes on a laptop; most of it is moto's per-request overhead. Fraud thresholds are dbt vars.

## Credentials

None are needed. No workflow uses static cloud keys. The optional `deploy` workflow assumes
an AWS role through GitHub OIDC and only runs when the repository variables
`AWS_DEPLOY_ROLE_ARN` and `TF_STATE_BUCKET` are set; it plans by default and applies only when
dispatched with `apply = true`.

Bootstrapping the deploy role (one-off, from a workstation with admin credentials for the
target account): run `terraform apply` with `github_repository = "<owner>/<repo>"`,
`tf_state_bucket = "<existing state bucket>"` and, if the account has no GitHub OIDC provider
yet, `create_github_oidc_provider = true`. The role it creates can only be assumed by jobs in
the repository's `aws-dev` environment; it may manage resources named with the project prefix,
KMS keys tagged `Project = fintech-broking-cards-aws`, and the state bucket's
`fintech-broking-cards-aws/` key prefix. The workflow passes the same `github_repository` and
`tf_state_bucket` values (as `TF_VAR_*`) so later plans do not try to remove the role.
