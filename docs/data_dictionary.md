# Data dictionary

Layers: **raw** Iceberg tables (`lake.raw_*`, real public data) -> **simulated** Iceberg tables
written by the event pipeline (`lake.business_events`, `lake.ledger_entries`, ...) -> dbt
**staging** views -> **intermediate** tables -> **marts**. Locally the Iceberg catalog is SQLite
and the warehouse a directory; DuckDB reads the tables through the dbt-duckdb iceberg plugin.

Units: INR amounts are rupees unless a column says otherwise (`_crore` = 10^7, `_lakh` = 10^5,
`_thousand` = 10^3). USD amounts in `_musd` are millions, `_bn` billions.

## Raw (real)

### `lake.raw_sec_companyfacts`
One row per XBRL fact for the us-gaap concepts in `fbc.sources.sec.FACT_CONCEPTS`.

| Column | Type | Meaning |
|---|---|---|
| cik | bigint | 1783879 (Robinhood Markets, Inc.) |
| taxonomy, concept, unit | string | e.g. `us-gaap`, `Revenues`, `USD` |
| period_start, period_end | date | Duration facts have both; instant facts only `period_end` |
| value | double | Reported value |
| fiscal_year, fiscal_period, form, accession, filed, frame | | Filing metadata from companyfacts |

### `lake.raw_hood_revenue_by_type`
| Column | Type | Meaning |
|---|---|---|
| accession, form, report_date | | Filing the fact was read from |
| period_start, period_end | date | Three-month or twelve-month period |
| revenue_type | string | `total`, `transaction_based`, `net_interest`, `other` |
| value_usd | double | USD |

`transaction_based` and `other` come from facts on `srt:ProductOrServiceAxis`
(`hood:TransactionBasedRevenuesMember`, `us-gaap:FinancialServiceOtherMember`); `net_interest`
from undimensioned `InterestIncomeExpenseNet`; `total` from undimensioned `Revenues`.

### `lake.raw_hood_kpis`
| Column | Type | Meaning |
|---|---|---|
| report_date | date | Period end of the 10-Q / 10-K |
| funded_customers_m | double | Funded Customers, millions (end of period) |
| platform_assets_bn | double | Total Platform Assets (called AUC before 2025), USD billions (end of period) |
| net_deposits_bn | double | Net Deposits during the period, USD billions |
| net_deposits_period_months | int | 3 for 10-Qs, 12 for 10-Ks |
| arpu_usd | double | Annualised average revenue per user (as reported) |
| gold_subscribers_m | double | Robinhood Gold subscribers, millions |

### `lake.raw_nse_bhavcopy` (partitioned by `trade_date`)
Stock rows (`FinInstrmTp = STK`) of the NSE capital-market bhavcopy: `trade_date, symbol, isin,
series, instrument_name, open, high, low, close, prev_close, volume, turnover_inr, trades,
source_file`. Not redistributed.

### `lake.raw_rbi_psi`
Long format of the RBI Payment System Indicators release.

| Column | Meaning |
|---|---|
| release_month | Month of the release the value was read from |
| period_month | Month the value refers to (each release has the latest month, the month before and the same month a year earlier) |
| metric | `upi`, `imps`, `neft`, `card_payments`, `credit_card_payments`, `credit_card_pos`, `credit_card_others`, `debit_card_payments`, `total_digital_payments`, `credit_cards_outstanding`, `debit_cards_outstanding`, `upi_qr_codes`, `domestic_payment_frauds` |
| measure | `volume_lakh`, `value_crore`, `count_lakh` |
| value | double |

### `lake.raw_rbi_card_stats`
One row per bank (plus the `TOTAL` row) and month: `bank_group, bank_name, is_total_row,
atms_onsite, atms_offsite, pos_terminals, micro_atms, bharat_qr, upi_qr,
credit_cards_outstanding, debit_cards_outstanding, cc_pos_volume, cc_pos_value_thousand,
cc_ecom_volume, cc_ecom_value_thousand, cc_others_volume, cc_others_value_thousand,
cc_atm_cash_volume, cc_atm_cash_value_thousand, dc_pos_volume, dc_pos_value_thousand,
dc_ecom_volume, dc_ecom_value_thousand`.

## Simulated (all rows `simulated = true`)

### `lake.business_events` (partitioned by `business_date`)
One row per ledger request outcome, from the journal header items on the DynamoDB stream.

| Column | Meaning |
|---|---|
| txn_id | UUIDv5 of the idempotency key (stable across retries) |
| txn_type | `DEPOSIT`, `TRADE_BUY`, `TRADE_SELL`, `SETTLE_BUY`, `SETTLE_SELL`, `CARD_AUTH`, `CARD_PAYMENT` |
| status, reason | `POSTED`, or `REJECTED` with the failed constraint |
| business_date, event_ts | Simulated IST date and time (naive timestamps) |
| idempotency_key | Client-supplied key |
| customer_id, card_id | Simulated ids (`C00001`, `K000001`) |
| symbol, isin, side, qty, price, notional, fees, settlement_date | Orders; `price` is the real NSE VWAP of the day |
| trade_txn_id, trade_date | Settlements: the fill being settled |
| amount, channel, mcc, merchant_category, merchant_city | Deposits, card authorisations, bill payments |
| injected_fraud, fraud_scenario | Simulator ground truth for card frauds (never read by the rules) |
| stream_sequence | DynamoDB stream sequence number |

### `lake.ledger_entries` (partitioned by `business_date`)
One row per posted leg: `txn_id, leg_no, txn_type, status, business_date, event_ts, account,
unit, amount, idempotency_key, stream_sequence, simulated`. `unit` is `INR` or an ISIN
(share quantity). For every `txn_id` and `unit`, `sum(amount) = 0`.

Accounts: `CUST#<id>:CASH`, `CUST#<id>:CASH_PENDING`, `CUST#<id>:SEC`, `CUST#<id>:SEC_PENDING`,
`CARD#<id>:OUTSTANDING`, `CCP:CASH_PAYABLE`, `CCP:CASH_RECEIVABLE`, `CCP:SEC`,
`HOUSE:BROKERAGE_FEES`, `EXT:UPI_PAYIN`, `EXT:CCP_SETTLEMENT`, `EXT:CARD_NETWORK`
(see `src/fbc/ledger/model.py`).

### `lake.ledger_balances_snapshot`, `lake.recon_results`, `lake.sim_customers`, `lake.sim_cards`
The DynamoDB balances table at the end of the run; the reconciliation job's checks
(`check_name, scope, expected, actual, difference, status`); simulated customers
(`city, risk_profile`) and cards (`issuer_bank` drawn from the RBI issuer mix, `credit_limit`,
`statement_day`).

## Marts

| Model | Grain | Key columns |
|---|---|---|
| `fct_positions_mtm` | customer x ISIN x NSE session | `quantity` (settled + pending), `close_price` (real), `market_value`, `net_cash_out`, `fees`, `daily_pnl` = change in market value - net cash paid - fees |
| `mart_daily_pnl` | customer x session | `open_positions`, `market_value`, `daily_pnl`, `cumulative_pnl` |
| `mart_settlement_cash` | trade date | `settlement_date`, `expected_settlement_date` (next real NSE session), `pay_in_obligation`, `pay_out_receivable`, `net_pay_in`, `settlement_status` (`SETTLED`/`PENDING`/`PARTIAL`) |
| `mart_upi_growth_monthly` | month | `volume_mn`, `value_crore`, `avg_ticket_inr`, `volume_mom`, `volume_yoy`, `value_yoy` |
| `mart_credit_card_spend_per_card` | month x issuing bank (+ `ALL BANKS`) | `credit_cards_outstanding`, `cc_spend_inr` (PoS + e-commerce + other), `spend_per_card_inr`, `txns_per_card`, `avg_ticket_inr`, `ecom_share_of_spend`, `share_of_industry_spend`, `psi_credit_cards_outstanding` (cross-check) |
| `mart_robinhood_kpis` | quarter end | funded customers, platform assets, net deposits, revenue by type (USD m), `revenue_parts_reconcile`, `includes_derived_q4`, `funded_customers_yoy`, `revenue_yoy`, `annualized_net_deposit_rate` |
| `mart_ledger_reconciliation` | check | `source` (`python_recon_job` / `dbt`), `check_name`, `scope`, `expected`, `actual`, `status` |
| `fct_card_fraud_alerts` | card authorisation | `rule_velocity`, `rule_impossible_travel`, `rule_amount_spike`, `rule_night_risky_mcc`, `is_alert`, plus the simulator labels |
| `mart_fraud_rule_performance` | rule | `alerts`, `true_positives`, `labelled_frauds`, `precision`, `recall`, `alert_rate` |
| `mart_sim_card_spend_vs_rbi` | one row | simulated vs RBI spend per card per day, transactions per card per day and average ticket |

Fraud rules (thresholds are dbt vars in `dbt/dbt_project.yml`): **velocity** >= 4 authorisations
on a card within 10 minutes; **impossible travel** an in-person transaction in a different city
from the previous in-person one within 60 minutes; **amount spike** >= INR 20,000 and more than
8x the card's previous average; **night risky MCC** gambling (7995) or quasi-cash/crypto (6051)
between 00:00 and 05:00.
