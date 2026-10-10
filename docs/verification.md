# Verification

Everything below comes from runs on 9 October 2026 against live downloads: SEC EDGAR
(12 latest 10-Q/10-K filings), the 10 latest NSE sessions (25 September to 9 October 2026) and
the RBI releases linked on its statistics pages (12 Payment System Indicators releases,
September 2025 to August 2026, and 10 card-statistics releases, November 2025 to August 2026).
"Pipeline" means the dbt marts built by `uv run fbc run`.

**How the NSE and RBI files were obtained.** Those runs were made by an earlier version that
sent a desktop Chrome User-Agent string to NSE and the RBI. Neither serves its files to a client
that identifies itself honestly, and NSE's Terms of Use prohibit automated collection and the
use of its data for simulation ([DATA_LICENSE.md](../DATA_LICENSE.md)). That code has been
removed. Sections 2 to 5 are kept as the record of what the parsers and the ledger did on those
files; the current code does not download them and cannot reproduce those figures. Section 1
(SEC, which serves the declared User-Agent) was re-run on 10 October 2026 with the current code
and gives the same figures; section 6 describes that run.

## 1. Robinhood KPIs and revenue vs the published earnings releases

The pipeline reads KPIs from the 10-Q/10-K *Key Performance Metrics* table and revenue by type
from the XBRL instance documents. The independent reference is Robinhood's quarterly earnings
press release (Exhibit 99.1 to the 8-K, Item 2.02), which is a different document: rounded
figures in prose. Q4 values in the pipeline are **derived** (10-K full year minus Q1 to Q3), so
the Q4 rows test the derivation as well.

| Quarter | Metric | Pipeline (`mart_robinhood_kpis`) | Press release | Match |
|---|---|---|---|---|
| Q2 2026 | Funded Customers | 28.4 M | 28.4 M | yes |
| | Total Platform Assets | $368.7 B | $369 B | yes (rounding) |
| | Net Deposits | $21.7 B | $21.7 B | yes |
| | Transaction-based / net interest / other revenue | $776 M / $389 M / $143 M | $776 M / $389 M / $143 M | yes |
| | Total net revenues | $1,308 M | (table) $1,308 M | yes |
| Q1 2026 | Funded Customers, TPA, Net Deposits | 27.4 M, $307.3 B, $17.7 B | 27.4 M, $307 B, $17.7 B | yes |
| | Revenue by type | $623 M / $359 M / $85 M | $623 M / $359 M / $85 M | yes |
| Q4 2025 (derived) | Net Deposits | $15.9 B (68.1 - 18.0 - 13.8 - 20.4) | $15.9 B | yes |
| | Revenue by type | $776 M / $411 M / $96 M | $776 M / $411 M / $96 M | yes |
| | Total net revenues | $1,283 M | $1.28 B | yes |
| | Funded Customers | 27.0 M | 27.0 M | yes |
| | **Total Platform Assets** | **$322.1 B** | **$324 B** | **no** (-0.6 %) |
| Q4 2024 (derived) | Revenue by type | $672 M / $296 M / $46 M | $672 M / $296 M / $46 M | yes |
| | Total net revenues | $1,014 M | $1.01 B | yes |
| | Net Deposits, AUC, Funded Customers | $16.1 B, $192.9 B, 25.2 M | $16.1 B, $193 B, 25.2 M | yes |

Sources (SEC EDGAR, Robinhood Markets CIK 1783879): Q2 2026 release, accession
0001783879-26-000113 (`q22026robinhoodexhibit991.htm`); Q1 2026, 0001783879-26-000061;
Q4 2025, 0001783879-26-000013; Q4 2024, 0001783879-25-000045. Browse them at
<https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001783879&type=8-K>.

**The one mismatch.** The FY2025 10-K's Key Performance Metrics table reports Total Platform
Assets of $322.1 B at 31 December 2025; the Q4 2025 earnings release says $324 B. The pipeline
reports what the 10-K says. The difference is in the source documents, not in the parsing
(the parser reads the same table correctly for the other ten filings), and the release came
out a week before the 10-K, so it may reflect a later definition or a revision. It is left as
reported and flagged here.

**Gaps.** The 10-Q for Q3 2023 uses a different KPI table layout, which the parser does not
read (`sec_parse_gaps` in `runs/ingest_manifest.json`). The FY2023 10-K is parsed, but its
Q4 net deposits cannot be derived without the 2023 10-Qs, so the quarterly KPI series in the
mart starts in Q1 2024 (revenue by type goes back to Q1 2023). Revenue for
Q3 2022 (read as the prior-year column of the Q3 2023 10-Q) does not add up: "other revenue"
was tagged with a different member then. The raw DQ check `hood_revenue_parts_all_quarters`
warns about it and the mart marks it `revenue_parts_reconcile = false`; the site leaves it out.

## 2. RBI: two RBI publications against each other

There is no third-party publication of these numbers to compare against, so the check is
between two separately compiled RBI releases: the bank-wise *ATM, Acceptance Infrastructure
and Card Statistics* (summed over banks by the RBI's own Total row) and the system-level
*Payment System Indicators* (PSI).

| Month | Credit cards outstanding, card statistics | PSI Part III (lakh x 10^5) | Match |
|---|---|---|---|
| Aug 2026 | 124,053,544 | 124,053,544 (1,240.53544 lakh) | yes |
| Dec 2025 - Jul 2026 (8 months) | e.g. Jul 2026: 122,857,894 | 122,857,894 | yes, all 8 |
| **Nov 2025** | **115,001,607** | **114,876,170** (Dec 2025 release; 114,866,073 in the Nov release) | **no** (-0.11 %) |

| Month | Credit-card PoS spend, card statistics | PSI 4.1.1 "PoS based" | Other spend (e-com + others) vs PSI 4.1.2 "Others" |
|---|---|---|---|
| Aug 2026 | ₹79,355.09 crore | ₹79,355.09 crore | ₹123,093.55 crore = ₹123,093.55 crore |
| Jul 2026 | ₹75,740.37 crore | ₹75,740.37 crore | ₹132,445.33 crore = ₹132,445.33 crore |
| Jun 2026 | ₹74,262.07 crore | ₹74,262.07 crore | ₹127,099.61 crore = ₹127,099.61 crore |

The monthly transaction counts agree the same way (Aug 2026: 6,324.47 lakh in both).

**The one mismatch: November 2025.** The bank-wise total is 115,001,607 credit cards; the PSI
gives 1,148.66 lakh (114,866,073) in its November 2025 release and 1,148.76 lakh (114,876,170)
when it repeats the month in the December 2025 release. Both differ from the bank-wise total by
about 0.1 %, so this is not only a PSI revision; the two publications simply disagree for that
month, and the source does not say why. The pipeline keeps both values
(`mart_credit_card_spend_per_card.psi_credit_cards_outstanding` is the PSI figure from the
latest release), and the dbt test `assert_rbi_cards_cross_publication` (severity warn, 0.5 %
tolerance) passes because the gap is within tolerance.

**PSI revisions.** Each PSI release repeats the previous month and the same month a year
earlier. Across the 12 releases, every UPI month that appears in two releases has identical
volume in both (12 overlapping months, 0 revisions).

**UPI vs NPCI: not verified.** NPCI publishes UPI statistics itself, but its website returns
HTTP 403 to automated clients from this network (Akamai), so the RBI series could not be
compared with NPCI's. For reference, the pipeline's August 2026 figure is 24,508.96 lakh
transactions (24.51 billion), ₹29,82,356 crore, +22.5 % on August 2025.

## 3. NSE trading calendar

The 10 sessions in the window are the dates for which NSE serves a bhavcopy. 2 October 2026
returned HTTP 404: it is Gandhi Jayanti, a trading holiday, and T+1 settlement of the
1 October trades correctly moved to 5 October (`assert_settlement_is_t_plus_1`).

The NSE holiday calendar has since been committed (`fbc/sources/nse_calendar.py`): NSE's own
published trading holidays for 2024-2026, read from Internet Archive copies of its
`holiday-master` endpoint because nseindia.com does not answer this client. It lists
2 October 2026 (Mahatma Gandhi Jayanti), so the missing file and the published list agree, and
`expected_sessions(date(2026, 10, 9), 10)` returns exactly the 10 sessions of that window
(`tests/unit/test_nse_calendar.py`). The raw gate now requires every session the calendar
expects in the requested window (`nse_trading_days`, error, exit 3); before, it passed with 2
of 10.

## 4. The simulated ledger

Not a comparison with published figures (the activity is simulated) but the internal proof
the spec asks for. Default run: 10 sessions, 400 customers, 1,000 cards, seed 20261009.

| Check | Result |
|---|---|
| Postings / rejections / replays / key conflicts | 7,747 posted, 42 rejected by ledger constraints (39 orders, 3 card authorisations), 254 client retries answered as replays, 2 conflicting reuses of a key refused with 409 |
| At-least-once delivery | 1,500 stream records re-delivered to the Lambda; Firehose landed 27,843 leg rows and 8,084 event rows; after the Iceberg merge 26,638 and 7,789 remain (one per leg / per request) |
| Python reconciliation (39 checks) | all OK: trial balance zero for INR and all 30 ISINs; 4,550 account/unit pairs identical in DynamoDB and Iceberg; 1,532 positions and 400 customers' cash equal the order events; clearing payable and receivable equal unsettled trades; 908 card balances equal authorisations minus payments; no constraint violated |
| dbt checks | 32 reconciliation rows OK; `dbt build` PASS=79 WARN=0 ERROR=0 (23 models, 55 tests, 1 run hook) |
| Calibration | Simulated average card ticket ₹3,201.5 vs RBI August 2026 ₹3,201.0; 0.165 vs 0.164 transactions per card per day |
| Rerun | `tests/e2e` runs the pipeline twice on fixtures; lake row counts, ledger counters and every mart are identical. `tests/integration` re-sends every request to a persistent endpoint: 0 new postings, all replays, still reconciled. |

The fraud rules' precision and recall in `mart_fraud_rule_performance` are measured against
the simulator's own injected labels, so they say how well the rules find *these* synthetic
patterns and nothing about real-world fraud detection.

## 5. Independent reproduction

A second, independent run on 10 October 2026 started from an empty data directory (fresh
downloads of every source, same seed) and reproduced every figure above exactly: 7,747
postings, 42 rejections, 254 replays, 2 conflicts, 1,500 re-delivered records, 27,843 / 8,084
landed rows merged to 26,638 / 7,789, 71 reconciliation rows all OK, `dbt build` PASS=79
WARN=0 ERROR=0, and the same UPI, card and Robinhood headline numbers. The Q2 2026 and
Q4 2025 earnings releases were re-read directly from EDGAR for that review (funded customers
28.4 M, platform assets $369 B, net deposits $21.7 B, revenue $776 M / $389 M / $143 M =
$1,308 M; Q4 2025 platform assets $324 B against the 10-K's $322.1 B). That run took about
33 minutes because it shared the machine with the test suite; a 651 s unloaded run of that
version was quoted in the README at the time.

## 6. Current code, 10 October 2026

`uv run fbc run --report site` from an empty data directory with the default settings (seed
20261009, 10 sessions requested, 400 customers, 1,000 cards):

| Step | Result |
|---|---|
| SEC EDGAR | Served; 14 files. Every Robinhood figure in section 1 is unchanged (Q2 2026: 28.4 M, $368.7 B, $21.7 B, $1,308 M; Q4 2025: $322.1 B platform assets). |
| NSE | Not contacted (`FBC_NSE_DOWNLOAD=0`); no files in the raw directory, so the committed synthetic bhavcopies were used: 28 September to 1 October 2026, 4 sessions. Raw gate: `4 of 4 expected sessions 2026-09-28..2026-10-01; missing none`. |
| RBI | Listing page served; the first workbook came back as the JavaScript challenge page four times, so RBI was skipped (`refused` in the `fetch` block) and the synthetic workbooks were used. |
| Raw gate | 11 error checks passed; 1 of 4 warnings (`hood_revenue_parts_all_quarters`, Q3 2022, as in section 1) |
| Simulated ledger | 2,543 posted, 17 rejected, 81 replays, 1 key conflict; 500 records re-delivered; Firehose landed 9,539 leg rows and 2,648 event rows, 9,127 and 2,560 after the merge; 39 Python reconciliation checks and 32 dbt reconciliation rows all OK |
| dbt | PASS=78 WARN=1 ERROR=0; the warning is `assert_rbi_cards_cross_publication` (3 rows), expected on the independently generated synthetic workbooks (tests/fixtures/README.md) |
| Site | `sources`: SEC real, NSE synthetic, RBI synthetic; the UPI, card and price figures carry "synthetic fixture" labels |
| Time | 143 s end to end on a laptop |
