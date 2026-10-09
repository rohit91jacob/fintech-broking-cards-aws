# Broking + cards ledger on AWS (emulated)

[![ci](https://github.com/rohit91jacob/fintech-broking-cards-aws/actions/workflows/ci.yml/badge.svg)](https://github.com/rohit91jacob/fintech-broking-cards-aws/actions/workflows/ci.yml)
[![refresh](https://github.com/rohit91jacob/fintech-broking-cards-aws/actions/workflows/refresh.yml/badge.svg)](https://github.com/rohit91jacob/fintech-broking-cards-aws/actions/workflows/refresh.yml)
[![Live results](https://img.shields.io/badge/live%20results-GitHub%20Pages-0b5cad.svg)](https://rohit91jacob.github.io/fintech-broking-cards-aws/)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Data licences](https://img.shields.io/badge/data-see%20DATA__LICENSE-green.svg)](DATA_LICENSE.md)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![dbt 1.12](https://img.shields.io/badge/dbt-1.12-orange.svg)
![Iceberg](https://img.shields.io/badge/apache%20iceberg-pyiceberg%200.12-blueviolet.svg)
![Terraform](https://img.shields.io/badge/terraform-1.16-623ce4.svg)

An event-driven, double-entry ledger for a retail **stock broker** (Zerodha / Robinhood style)
and a **credit-card and bill-payments** app (CRED style), built the way it would run on AWS:

**API -> DynamoDB double-entry ledger (idempotency keys, conditional writes) -> DynamoDB
Streams -> Lambda -> Kinesis Data Firehose -> S3 / Apache Iceberg -> dbt (DuckDB locally,
Athena compiled in CI)**, with a reconciliation job proving that ledger balances equal positions,
T+1 settlement, card-fraud rules, and Terraform for the whole stack.

* **Real data.** Robinhood's KPIs and revenue by type from **SEC EDGAR** XBRL and filings;
  **NSE India** end-of-day prices; **RBI** payment statistics (UPI volumes and values, credit
  cards outstanding, card spend by bank). All downloaded at run time.
* **Simulated activity, clearly labelled.** Customer orders and card swipes are not public, so
  a seeded simulator generates them: fills at the real NSE daily VWAP, card spending sized from
  RBI averages. Every simulated row carries `simulated = true`.
* **Emulated cloud, real code paths.** Every AWS call is a real `boto3` call. Locally and in
  tests it runs on [moto](https://github.com/getmoto/moto) in-process; a CI job runs the same
  code over HTTP against LocalStack (or moto's server). **No AWS account is used anywhere.**
  The Terraform is validated (`fmt`, `validate`, `tflint`, `checkov`), not deployed.
* **Proof.** Robinhood figures match the published earnings releases for every compared metric
  but one (a $322.1 B vs $324 B platform-assets difference between the 10-K and the release);
  two RBI publications agree for 9 of 10 months; the ledger reconciles on 39 + 32 checks. See
  [docs/verification.md](docs/verification.md).

## Results (run of 9 October 2026)

| | |
|---|---|
| UPI, August 2026 (RBI) | **24.51 bn** transactions, ₹29.8 lakh crore, **+22.5 %** year on year |
| Credit cards outstanding, August 2026 (RBI) | **124.1 M**; spend per card **₹16,319** a month |
| Robinhood, Q2 2026 (SEC 10-Q) | **28.4 M** funded customers, **$368.7 B** platform assets, **$21.7 B** net deposits, **$1,308 M** revenue |
| Simulated ledger, 25 Sep - 9 Oct 2026 | **7,747** postings, **42** rejected by ledger constraints, **254** idempotent replays, **1,500** stream records redelivered and de-duplicated; reconciliation **OK** |
| Full real-data run (`fbc run`, laptop) | 651 s end to end (ingest about 4 min, simulation 6 min, dbt and site the rest); `dbt build` on that lake: PASS=79, WARN=0, ERROR=0 |

The [results site](https://rohit91jacob.github.io/fintech-broking-cards-aws/) is rebuilt every
weekday evening (IST) by [`refresh.yml`](.github/workflows/refresh.yml).

## Architecture

```mermaid
flowchart LR
  subgraph real["Real public data (downloaded at run time)"]
    SEC["SEC EDGAR<br/>companyfacts + 10-Q/10-K"]
    NSE["NSE bhavcopy<br/>(EOD prices)"]
    RBI["RBI PSI +<br/>card statistics"]
  end
  subgraph sim["Simulated, seeded"]
    PROD["Producer<br/>orders, deposits,<br/>card auths, bill pay"]
  end
  subgraph aws["AWS (emulated: moto / LocalStack)"]
    API["API Gateway + Lambda<br/>ledger API"]
    DDB[("DynamoDB<br/>journal | balances | idempotency")]
    STR["DynamoDB Streams"]
    LAM["Lambda<br/>stream processor"]
    FH["Kinesis Firehose<br/>x2"]
    S3[("S3 landing")]
  end
  ICE[("Apache Iceberg<br/>lake.* tables")]
  REC["Reconciliation job<br/>DynamoDB vs Iceberg vs events"]
  DBT["dbt<br/>DuckDB (local) / Athena (compiled)"]
  MARTS["Marts: P&L + MTM, T+1 cash,<br/>UPI growth, spend per card,<br/>Robinhood KPIs, recon, fraud"]
  SITE["Static results site<br/>GitHub Pages"]

  SEC & NSE & RBI -->|ingest + raw DQ gate| ICE
  NSE -. prices .-> PROD
  RBI -. calibration .-> PROD
  PROD -->|Idempotency-Key| API --> DDB --> STR --> LAM --> FH --> S3 -->|merge on keys| ICE
  DDB --> REC
  ICE --> REC -->|lake DQ gate| ICE
  ICE --> DBT --> MARTS --> SITE
```

| Component | What it does | Code |
|---|---|---|
| Sources | SEC companyfacts, XBRL instances (revenue on `srt:ProductOrServiceAxis`), KPI tables; NSE UDiFF bhavcopy; RBI PSI and bank-wise card statistics | `src/fbc/sources/` |
| Ledger API | API Gateway-style Lambda handler: deposits, orders, T+1 settlements, card authorisations, bill payments, balance reads | `src/fbc/ledger/api.py` |
| Ledger service | One `TransactWriteItems` per posting: idempotency record (`attribute_not_exists`), journal header + legs, `ADD balance` with conditions (no overdraft, no short sale, credit limit). Replays, rejections and key conflicts are distinguished from the cancellation reasons. | `src/fbc/ledger/service.py`, `model.py` |
| Stream processor | Lambda for DynamoDB Streams: headers -> business events, legs -> ledger entries, `PutRecordBatch` to two Firehose streams, partial batch failures | `src/fbc/ledger/stream.py` |
| Event source mapping (emulated) | Reads shards, invokes the Lambda in batches, re-delivers 5 % of batches to exercise at-least-once delivery | `StreamPoller` in `stream.py` |
| Iceberg sink | Plays Firehose's Iceberg destination: lands S3 JSON lines into Iceberg, partitioned by `business_date`, merged on `(txn_id, leg_no)` / `txn_id` | `src/fbc/lake/sink.py`, `iceberg.py` |
| Simulator | Seeded customers, cards, orders, card swipes, retries, conflicting keys and four fraud scenarios | `src/fbc/sim/` |
| Reconciliation | Trial balance, DynamoDB vs Iceberg, positions, customer cash, clearing obligations, card balances, constraints | `src/fbc/recon.py` |
| Data quality | Raw gate (11 error + 3 warn checks) after ingest, lake gate (7 checks) after the pipeline, 55 dbt tests on the models | `src/fbc/quality/checks.py`, `dbt/` |
| dbt | staging -> intermediate -> marts, DuckDB over Iceberg locally, Athena target compiled in CI | `dbt/` |
| Infrastructure | Terraform modules: KMS, DynamoDB, S3 + Glue + Iceberg tables, Firehose (Iceberg destination), Lambda + API Gateway + DLQ, Athena, budgets and alarms, GitHub OIDC | `infra/terraform/` |
| Site | Static HTML with inline SVG charts and table views | `src/fbc/report.py` |

## Tech stack

| Layer | Tool (version in `uv.lock`) |
|---|---|
| Language | Python 3.12, managed by uv |
| AWS SDK / emulator | boto3 1.43, moto 5.2 (in-process and HTTP server), LocalStack 2026.09 (CI, optional) |
| Table format | Apache Iceberg via pyiceberg 0.12 (SQLite catalog, local warehouse) |
| Query / transform | DuckDB 1.5.6, dbt-core 1.12, dbt-duckdb 1.11 (iceberg plugin), dbt-athena 1.11 (compile only) |
| Files | pyarrow 26, openpyxl 3.1, requests 2.34 |
| Infrastructure | Terraform 1.16, AWS provider ~> 6.68, tflint 0.64 + AWS ruleset 0.49, checkov |
| Quality | pytest 9, ruff 0.16, pre-commit, gitleaks, actionlint |
| CI / scheduling | GitHub Actions, GitHub Pages |

## Data sources

| Source | What | Licence / terms | Refresh cadence | Known gaps |
|---|---|---|---|---|
| [SEC EDGAR](https://www.sec.gov/edgar/search/) companyfacts + submissions APIs, 10-Q/10-K XBRL instances and HTML | Robinhood (HOOD, CIK 1783879): revenues, revenue by type, funded customers, platform assets (AUC), net deposits, ARPU, Gold subscribers | Public; descriptive User-Agent required, <= 10 req/s | Quarterly filings; fetched on every refresh (filings cached) | Quarterly KPIs start in Q1 2024 (older 10-Q layout not parsed); Q3 2022 revenue split does not add up (old XBRL member); Q4 values derived from the 10-K |
| [NSE](https://www.nseindia.com/) capital-market bhavcopy (UDiFF, July 2024 onwards) | Daily OHLC, volume, turnover per stock | Not licensed for redistribution: downloaded at run time, never committed or published | Every trading day (evening IST); refresh runs 20:00 IST Mon-Fri | UDiFF only (older format not parsed); prices only for the 30 most-traded EQ stocks are used |
| [RBI](https://www.rbi.org.in/) Payment System Indicators | UPI, IMPS, NEFT, card payments (volume, value), cards outstanding, payment frauds | RBI website terms: no caching/framing without permission; files used at run time only, derived figures published with attribution | Monthly (about 4-6 weeks after month end) | Only the ~12 releases linked on the page are fetched (no archive crawl) |
| RBI ATM, Acceptance Infrastructure and Card Statistics | Bank-wise credit cards outstanding and credit/debit card spend | As above | Monthly | 10 months linked at the time of the run |
| NPCI UPI product statistics | (not used) | | | Blocked for automated clients (HTTP 403); UPI comes from the RBI series |

Details and attribution: [DATA_LICENSE.md](DATA_LICENSE.md).

## Data model

* **Raw (real):** `lake.raw_sec_companyfacts`, `raw_hood_revenue_by_type`, `raw_hood_kpis`,
  `raw_nse_bhavcopy` (partitioned by trade date), `raw_rbi_psi`, `raw_rbi_card_stats`.
* **Ledger (simulated):** `lake.business_events` (one row per request outcome) and
  `lake.ledger_entries` (one row per leg), both partitioned by `business_date`;
  `ledger_balances_snapshot`, `recon_results`, `sim_customers`, `sim_cards`.
* **Marts:** `fct_positions_mtm`, `mart_daily_pnl`, `mart_settlement_cash`,
  `mart_upi_growth_monthly`, `mart_credit_card_spend_per_card`, `mart_robinhood_kpis`,
  `mart_ledger_reconciliation`, `fct_card_fraud_alerts`, `mart_fraud_rule_performance`,
  `mart_sim_card_spend_vs_rbi`.

Double entry: every posting's legs sum to zero per unit (INR, or an ISIN for share quantities).
A buy moves cash from `CUST#c:CASH` to `CCP:CASH_PAYABLE` and fees, and shares from `CCP:SEC` to
`CUST#c:SEC_PENDING`; T+1 settlement moves the shares to `CUST#c:SEC` and pays the clearing
corporation. A card swipe adds to `CARD#k:OUTSTANDING` only if the balance stays within the
limit. Full column list: [docs/data_dictionary.md](docs/data_dictionary.md).

## Quickstart

Prerequisites: [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv). No Docker, no
cloud account. Terraform and tflint are only needed for the infrastructure checks.

```bash
git clone https://github.com/rohit91jacob/fintech-broking-cards-aws && cd fintech-broking-cards-aws
uv sync --frozen --all-extras
cp .env.example .env              # optional; set FBC_SEC_USER_AGENT to your name + e-mail

# Offline, on the committed fixtures (about 3 minutes):
make run-fixtures                 # -> site-fixtures/index.html

# Real data (downloads about 60 MB; 10-15 minutes on a laptop):
make run                          # ingest -> simulate -> dbt build -> site/
make reconcile                    # print the reconciliation; exit 4 on any break

make help                         # every target
```

Tests: `make test-unit` (in-process moto), `make test-e2e` (whole pipeline on fixtures plus an
identical rerun), `make test-integration` (HTTP endpoint; LocalStack if `FBC_AWS_ENDPOINT_URL`
is set, otherwise moto's server). Infrastructure: `make infra-check`.

## Local emulator -> real AWS

| Here | On AWS | Notes |
|---|---|---|
| moto `mock_aws` in-process; moto server or LocalStack over HTTP | the same API calls | `FBC_AWS_ENDPOINT_URL` selects the endpoint; code is identical |
| Ledger API called in-process with API Gateway v2 events | API Gateway HTTP API (IAM auth) -> Lambda `fbc.lambdas.api_handler` | `infra/terraform/modules/compute` |
| DynamoDB tables `fbc-dev-ledger-*` | DynamoDB on-demand, PITR, KMS, TTL on idempotency keys | `modules/ledger` |
| `StreamPoller` | Lambda event source mapping (batch 100, bisect, partial failures, DLQ) | |
| Firehose -> S3 JSON lines + `fbc.lake.sink` | Firehose **Iceberg destination** with `unique_keys` upserts into Glue tables | Emulators only support the S3 destination |
| pyiceberg `SqlCatalog` (SQLite) + local directory | AWS Glue Data Catalog + S3 bucket `fbc-dev-lake` | `modules/lake` |
| dbt-duckdb reading Iceberg | dbt-athena on Athena engine v3 (compiled in CI) | `make dbt-athena-compile` |
| GitHub Pages site | (same) | |
| moto's `TransactWriteItems` + a shim | DynamoDB transactions | moto deep-copies every table per transaction; `fbc/aws/moto_patches.py` checks all conditions first, then applies (same all-or-nothing result), tested in `tests/unit/test_moto_patches.py` |

## Cost notes

Nothing here costs money as shipped: the cloud is emulated and CI runs on free GitHub runners.
If the Terraform were applied, the dev stack is pay-per-use: DynamoDB on-demand, Lambda,
Firehose (per GB ingested, Iceberg destination), S3, Athena (per TB scanned, capped at 1 GiB
per query by the workgroup), KMS ($1 a month per key) and CloudWatch Logs. At this project's
volumes (thousands of events a day) that is a few dollars a month, mostly KMS, Logs and
Firehose minimums. Guardrails in `modules/guardrails`: a monthly AWS Budget (default $25,
alerts at 50/80/100 % actual and 100 % forecast), alarms on Lambda errors, stream iterator age,
DynamoDB throttles, DLQ depth and Firehose freshness, reserved concurrency on both functions,
lifecycle rules on every bucket. These are estimates, not measured bills.

## Limitations

* **Emulated, not deployed.** The Terraform passes `validate`, tflint and checkov (23 skips, each
  justified inline) but has never been applied. LocalStack's Firehose has no Iceberg
  destination, so the S3-landing-plus-merge step stands in for it.
* **LocalStack in CI is optional.** Newer LocalStack images need an auth token; without the
  `LOCALSTACK_AUTH_TOKEN` secret the CI emulator job runs the same HTTP test against moto's
  server. The LocalStack variant has not been run (no Docker locally).
* **The simulation is a model.** Brokerage is min(₹20, 0.03 %), statutory charges are not
  modelled, only settled shares can be sold, there are no corporate actions, and the fraud
  rules are scored against the simulator's own labels.
* **Speed.** The default run (about 7,800 postings) takes several minutes because moto
  serialises every request; real DynamoDB would not be the bottleneck.
* **Sources.** NPCI is not used (blocked); the RBI history is limited to the releases the
  listing pages link to; Robinhood quarterly KPIs start in Q1 2024.

## Project structure

```text
src/fbc/            cli, config, http, ingest, pipeline, recon, report, lambdas
  aws/              boto3 clients, emulator backend, resource creation, moto shim
  ledger/           model (postings, legs), service (DynamoDB), api, stream (Lambda + poller)
  lake/             iceberg helpers, Firehose-to-Iceberg sink
  sim/              calibration from real data, seeded producer
  sources/          sec, nse, rbi
  quality/          data-quality gates
dbt/                staging / intermediate / marts, tests, profiles (dev, ci, athena)
infra/terraform/    root module + modules/{kms,ledger,lake,streaming,compute,analytics,guardrails,ci_oidc}
tests/              unit, e2e, integration, fixtures (real SEC excerpts, synthetic NSE/RBI layouts)
scripts/            fixture builders, Lambda packaging
docs/               data_dictionary.md, runbook.md, verification.md
.github/workflows/  ci, refresh (weekdays), keepalive, deploy (manual, OIDC, gated)
```

## License

Code: MIT. Data: see [DATA_LICENSE.md](DATA_LICENSE.md). Not investment advice; not affiliated
with Robinhood, NSE, RBI, NPCI, Zerodha or CRED.
