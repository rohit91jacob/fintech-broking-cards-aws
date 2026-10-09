# Data licences and attribution

This repository combines three real public sources with a **simulated** event stream. What is
committed, what is only downloaded at run time, and why:

| Source | Terms | In this repository | Published on the results site |
|---|---|---|---|
| **U.S. SEC EDGAR**: Robinhood Markets, Inc. (CIK 1783879) XBRL company facts, submissions, 10-Q/10-K filings | Information on SEC.gov is public; the SEC asks automated clients to declare a User-Agent and stay under 10 requests/second ([SEC privacy and security policy](https://www.sec.gov/privacy), [accessing EDGAR data](https://www.sec.gov/os/accessing-edgar-data)) | **Small trimmed fixtures** in `tests/fixtures/raw/sec/` (selected companyfacts concepts, revenue facts of six XBRL instances, and the verbatim Key Performance Metrics paragraph of six filings). Built by `scripts/build_sec_fixtures.py`. | Derived quarterly KPIs and revenue by type |
| **National Stock Exchange of India**: capital-market bhavcopy (UDiFF, `nsearchives.nseindia.com`) | NSE's website terms do not grant a licence to reproduce or redistribute its data; NSE licenses market data commercially. | **Nothing.** Files are downloaded at run time into the git-ignored data directory. Tests use **synthetic** files in the same layout (`tests/fixtures/raw/nse/`, symbols `SYN01`..., ISINs `INSYN...`). | No prices. Prices are used only to value *simulated* positions; the site shows book-level aggregates. |
| **Reserve Bank of India**: Payment System Indicators; ATM, Acceptance Infrastructure and Card Statistics (monthly XLSX from `rbidocs.rbi.org.in`) | The RBI website's terms prohibit caching or framing its content without permission. | **Nothing.** Files are downloaded at run time. Tests use **synthetic** workbooks in the same layout (`tests/fixtures/raw/rbi/`, titled "SYNTHETIC TEST FIXTURE", banks `SYNTHETIC ... BANK`). | Derived monthly aggregates (UPI volume/value and growth, credit-card spend per card) with attribution |

The pipeline keeps working copies of the NSE and RBI files in its local data directory, and the
scheduled refresh keeps them in the repository's private GitHub Actions cache so it does not
re-download unchanged files. They are never committed, published or served. The site
publishes only figures derived from them, with attribution. If you reuse this project for
anything beyond personal study, check those terms with NSE and the RBI yourself.

UPI statistics are taken from the RBI's Payment System Indicators. NPCI's own statistics page
blocks automated clients (HTTP 403 from an Akamai edge), so NPCI is not used directly.

## Simulated data

Customer orders, fills, deposits, card authorisations, bill payments, balances, credit
limits, cities, merchant categories and the fraud labels are **simulated** by
`src/fbc/sim/producer.py` with a fixed seed (`FBC_SEED`, default 20261009). Every simulated row
carries `simulated = true` and every simulated mart and site section says so. The simulation is
*calibrated* on real figures (NSE daily VWAPs; RBI transactions per card, average ticket
sizes and issuer mix) but describes no real person, account or company.

## Attribution

If you publish anything built with this pipeline, credit the sources, for example:

> Contains data from U.S. SEC EDGAR; Reserve Bank of India, Payment System Indicators and
> ATM/POS/Card Statistics (derived figures). Trades and card transactions are simulated.

"Robinhood", "NSE", "RBI", "UPI", "Zerodha" and "CRED" are names of their owners. This project
has no affiliation with any of them, and nothing here is investment advice.

Code, documentation and the synthetic fixtures are MIT-licensed ([LICENSE](LICENSE)).
