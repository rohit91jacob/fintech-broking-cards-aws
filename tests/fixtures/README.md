# Test fixtures

`raw/` mirrors the layout of the pipeline's raw download directory, so tests copy it into a
temporary `FBC_DATA_DIR/raw` and run `fbc ingest --offline`.

| Path | Real or synthetic | Why |
|---|---|---|
| `raw/sec/` | **Real**, trimmed (SEC EDGAR, public) | Rebuilt from a full download with `scripts/build_sec_fixtures.py`. Six filings: 10-Qs for Q1-Q3 2025 and Q1-Q2 2026 and the FY2025 10-K. |
| `raw/nse/` | **Synthetic** | NSE does not permit redistribution. Same UDiFF columns; symbols `SYN01`-`SYN40`, ISINs `INSYN...`, one non-EQ row, and no file for 2 Oct 2026 (an exchange holiday). |
| `raw/rbi/` | **Synthetic** | RBI terms prohibit caching. Same sheet layout (header rows, numbered columns, the stray cell before one bank row, the Total row); every workbook is titled "SYNTHETIC TEST FIXTURE". |

Regenerate the synthetic files with `uv run python scripts/make_synthetic_fixtures.py --out tests/fixtures/raw`.
Each file is under 300 KB (enforced in CI and by pre-commit).

The synthetic PSI and card-statistics workbooks are generated independently, so the warn-level
dbt test `assert_rbi_cards_cross_publication` reports 3 rows on fixture runs. On real data it
passes (see docs/verification.md).
