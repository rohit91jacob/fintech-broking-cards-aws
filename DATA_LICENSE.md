# Data licences and attribution

This repository combines real public data (SEC EDGAR) with **synthetic** stand-ins for the NSE
and RBI files (neither serves an honestly identified automated client, and NSE's terms forbid
both automated collection and use in a simulation) and a **simulated** event stream. What is
committed, what is only downloaded at run time, and why:

| Source | Terms | In this repository | Published on the results site |
|---|---|---|---|
| **U.S. SEC EDGAR**: Robinhood Markets, Inc. (CIK 1783879) XBRL company facts, submissions, 10-Q/10-K filings | Information on SEC.gov is public; the SEC asks automated clients to declare a User-Agent and stay under 10 requests/second ([SEC privacy and security policy](https://www.sec.gov/privacy), [accessing EDGAR data](https://www.sec.gov/os/accessing-edgar-data)) | **Small trimmed fixtures** in `tests/fixtures/raw/sec/` (selected companyfacts concepts, revenue facts of six XBRL instances, and the verbatim Key Performance Metrics paragraph of six filings). Built by `scripts/build_sec_fixtures.py`. | Derived quarterly KPIs and revenue by type |
| **National Stock Exchange of India**: capital-market bhavcopy (UDiFF, `nsearchives.nseindia.com`) | NSE's Terms of Use prohibit automated collection and any use of its data for simulation (quoted below); content may not be copied or stored "without prior written permission of NSE". NSE licenses market data commercially. | **Nothing.** The download is **off by default** (`FBC_NSE_DOWNLOAD=0`). Tests and default runs use **synthetic** files in the same layout (`tests/fixtures/raw/nse/`, symbols `SYN01`..., ISINs `INSYN...`). | No prices. The simulated book is priced at the synthetic prices, and the site says so. |
| **Reserve Bank of India**: Payment System Indicators; ATM, Acceptance Infrastructure and Card Statistics (monthly XLSX from `rbidocs.rbi.org.in`) | The RBI website's terms prohibit caching or framing its content without permission and reserve the right "to deny or restrict access to this Web Site to any particular person". | **Nothing.** The pipeline asks for the files at run time, but the RBI's document server does not serve them to an automated client (below). Runs then use workbooks already in the git-ignored raw directory, else **synthetic** workbooks in the same layout (`tests/fixtures/raw/rbi/`, titled "SYNTHETIC TEST FIXTURE", banks `SYNTHETIC ... BANK`). | Derived monthly aggregates (UPI volume/value and growth, credit-card spend per card) with attribution; when the synthetic workbooks were used, the same tiles marked "SYNTHETIC fixture, not RBI data" |

Files in the local raw directory are never committed, published or served; the site publishes
only derived figures. If you reuse this project for anything beyond personal study, check the
terms with NSE and the RBI yourself.

## Automated access: what the sources do with an honest User-Agent

Every NSE and RBI request sends `fintech-broking-cards-aws/<version>
(+https://github.com/rohit91jacob/fintech-broking-cards-aws)` (override with `FBC_USER_AGENT`);
SEC requests send `FBC_SEC_USER_AGENT`, which SEC asks to include a contact e-mail. The
pipeline never sends a browser User-Agent. Tested on 10 October 2026:

| Endpoint | With the honest User-Agent | What the pipeline does |
|---|---|---|
| SEC EDGAR (`data.sec.gov`, `www.sec.gov/Archives`) | Served | Downloads companyfacts, submissions and the latest filings |
| NSE archive (`nsearchives.nseindia.com/content/cm/...csv.zip`) | No response: the connection is accepted and nothing comes back within 30 s (the same with `curl/8` and `python-requests` User-Agents). `www.nseindia.com` pages and APIs behave the same; only `robots.txt` is served. | Does not ask (the download is off). If switched on, the first unanswered request raises `fbc.http.Refused` and NSE is skipped. |
| RBI listing pages (`www.rbi.org.in/Scripts/PSIUserView.aspx`, `ATMView.aspx`) | Served | Reads the workbook links |
| RBI workbooks (`rbidocs.rbi.org.in/rdocs/.../*.XLSX`) | HTTP 200 with a JavaScript challenge page (F5 "TSPD": "This question is for testing whether you are a human visitor and to prevent automated spam submission") instead of the workbook. `www.rbi.org.in/robots.txt` answers HTTP 418 "Unauthorised Access". | Raises `Refused` after four attempts and skips RBI |

The same NSE and RBI URLs did return the ZIP and XLSX files when the request carried a desktop
Chrome User-Agent string, so the refusal is aimed at non-browser clients, not at the network.
The project does not work around it. When NSE or RBI is skipped, `fbc ingest` logs why and
records it in its `fetch` output and in `runs/ingest_manifest.json` (`sources`), then carries on
with files already in the raw directory or the committed synthetic fixtures. The results site
labels every NSE- or RBI-derived figure built from the fixtures as synthetic.

**NSE's Terms of Use** ([www.nseindia.com/static/nse-terms-of-use](https://www.nseindia.com/static/nse-terms-of-use),
"Updated on: 29/10/2025"; read from the
[Internet Archive copy of 1 October 2026](https://web.archive.org/web/20261001094837/https://www.nseindia.com/static/nse-terms-of-use)
because nseindia.com does not answer this client) say:

> User is prohibited to conduct any systematic or automated data collection activities
> (including scraping, data mining, data extraction and data harvesting) on or in relation to
> our Website / Mobile Application.

> User acknowledges, agrees and undertakes that it shall not, in any form and manner, use or
> make the data or Content available on the Website / Mobile Application, for any gaming,
> virtual trading or simulation activities under any circumstances whatsoever.

The first rules out the scheduled download. The second rules out pricing this project's
simulated broking book with NSE prices however the files are obtained. That is why the download
is off by default, why this repository does not suggest fetching the files by hand, and why the
default simulation runs on synthetic prices. `FBC_NSE_DOWNLOAD=1` exists only for someone with
NSE's written permission. NSE's `robots.txt` allows crawling, but that does not override the
Terms of Use.

**Earlier versions** of this project (up to 10 October 2026) sent a desktop Chrome User-Agent
string to NSE and the RBI. The 9 October 2026 NSE and RBI figures in
[docs/verification.md](docs/verification.md) were obtained that way, and the simulation was
priced with those NSE files. That code has been removed. The scheduled refresh's cache key was
changed at the same time, so the raw-download caches made by the earlier version (holding those
NSE and RBI files) are no longer restored; they can be deleted from the repository's Actions
caches.

The NSE trading calendar used by the raw data-quality gate (`fbc/sources/nse_calendar.py`) is
NSE's published list of trading holidays (dates and names), read from Internet Archive copies of
NSE's `holiday-master` endpoint; the module lists the snapshots.

UPI statistics are taken from the RBI's Payment System Indicators. NPCI's own statistics page
blocks automated clients (HTTP 403 from an Akamai edge), so NPCI is not used directly.

## Simulated data

Customer orders, fills, deposits, card authorisations, bill payments, balances, credit
limits, cities, merchant categories and the fraud labels are **simulated** by
`src/fbc/sim/producer.py` with a fixed seed (`FBC_SEED`, default 20261009). Every simulated row
carries `simulated = true` and every simulated mart and site section says so. The simulation is
*calibrated* on the raw NSE and RBI tables (daily VWAPs; transactions per card, average ticket
sizes and issuer mix), which by default hold the synthetic fixtures, and describes no real
person, account or company.

## Attribution

If you publish anything built with this pipeline, credit the sources, for example:

> Contains data from U.S. SEC EDGAR; Reserve Bank of India, Payment System Indicators and
> ATM/POS/Card Statistics (derived figures). Trades and card transactions are simulated.

Leave out the RBI part when the run used the synthetic RBI workbooks (the site's footer does).

"Robinhood", "NSE", "RBI", "UPI", "Zerodha" and "CRED" are names of their owners. This project
has no affiliation with any of them, and nothing here is investment advice.

Code, documentation and the synthetic fixtures are MIT-licensed ([LICENSE](LICENSE)).
