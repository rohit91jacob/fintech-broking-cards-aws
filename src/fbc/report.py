"""Static results site (``fbc report --out site``) built from the dbt marts and run summaries.

Published content is limited to aggregates: no raw NSE prices and no RBI tables are
reproduced, only derived figures with attribution. Everything simulated is labelled, and so is
every figure built from the committed synthetic NSE/RBI fixtures (the ingest manifest says
which sources those were).
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from fbc.config import Settings
from fbc.ingest import read_manifest

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SERIES = [BLUE, ORANGE, AQUA]


def _q(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float) or hasattr(value, "as_integer_ratio"):
        v = float(value)
        if v.is_integer() and abs(v) < 1e15 and digits <= 1 and abs(v) < 1000:
            return f"{v:,.0f}"
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        return f"{v:,.{digits}f}"
    return html.escape(str(value))


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:+.1f}%"


def _share(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"


def _line_chart(points: list[tuple[str, float]], title: str, unit: str, kind: str = "line") -> str:
    """Single-series line or bar chart as inline SVG with hover titles."""
    if not points:
        return "<p class='muted'>No data.</p>"
    w, h, pad_l, pad_b, pad_t = 720, 240, 64, 28, 16
    vals = [v for _, v in points]
    lo = min(0.0, min(vals))
    hi = max(vals) or 1.0
    span = hi - lo or 1.0
    n = len(points)
    step = (w - pad_l - 12) / max(n, 1)

    def y(v: float) -> float:
        return pad_t + (h - pad_t - pad_b) * (1 - (v - lo) / span)

    grid = []
    for i in range(5):
        gv = lo + span * i / 4
        gy = y(gv)
        grid.append(
            f"<line x1='{pad_l}' x2='{w - 8}' y1='{gy:.1f}' y2='{gy:.1f}' class='grid'/>"
            f"<text x='{pad_l - 6}' y='{gy + 4:.1f}' class='axis' text-anchor='end'>{_fmt(gv, 0 if span > 20 else 1)}</text>"
        )
    marks = []
    if kind == "bar":
        bw = max(2.0, step - 4)
        for i, (label, v) in enumerate(points):
            x = pad_l + i * step + 2
            top, base = (y(v), y(0)) if v >= 0 else (y(0), y(v))
            marks.append(
                f"<rect x='{x:.1f}' y='{top:.1f}' width='{bw:.1f}' height='{max(1.0, base - top):.1f}' rx='2' fill='{BLUE}'>"
                f"<title>{html.escape(label)}: {_fmt(v)} {unit}</title></rect>"
            )
    else:
        coords = [(pad_l + i * step + step / 2, y(v)) for i, (_, v) in enumerate(points)]
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{x:.1f},{yy:.1f}" for i, (x, yy) in enumerate(coords)
        )
        marks.append(f"<path d='{path}' fill='none' stroke='{BLUE}' stroke-width='2'/>")
        for (x, yy), (label, v) in zip(coords, points, strict=True):
            marks.append(
                f"<circle cx='{x:.1f}' cy='{yy:.1f}' r='4' fill='{BLUE}' stroke='var(--surface)' stroke-width='2'>"
                f"<title>{html.escape(label)}: {_fmt(v)} {unit}</title></circle>"
            )
    ticks = []
    every = max(1, n // 8)
    for i, (label, _) in enumerate(points):
        if i % every == 0 or i == n - 1:
            ticks.append(
                f"<text x='{pad_l + i * step + step / 2:.1f}' y='{h - 8}' class='axis' text-anchor='middle'>{html.escape(label)}</text>"
            )
    return (
        f"<figure><figcaption>{html.escape(title)}</figcaption>"
        f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{html.escape(title)}'>"
        + "".join(grid + marks + ticks)
        + "</svg></figure>"
    )


def _stacked_bars(
    rows: list[dict[str, Any]], keys: list[tuple[str, str]], label_key: str, title: str
) -> str:
    if not rows:
        return "<p class='muted'>No data.</p>"
    w, h, pad_l, pad_b, pad_t = 720, 260, 64, 28, 16
    totals = [sum(float(r[k] or 0) for k, _ in keys) for r in rows]
    hi = max(totals) or 1.0
    n = len(rows)
    step = (w - pad_l - 12) / n
    bw = max(4.0, step - 6)

    def y(v: float) -> float:
        return pad_t + (h - pad_t - pad_b) * (1 - v / hi)

    out = []
    for i in range(5):
        gv = hi * i / 4
        out.append(
            f"<line x1='{pad_l}' x2='{w - 8}' y1='{y(gv):.1f}' y2='{y(gv):.1f}' class='grid'/>"
            f"<text x='{pad_l - 6}' y='{y(gv) + 4:.1f}' class='axis' text-anchor='end'>{gv:,.0f}</text>"
        )
    for i, r in enumerate(rows):
        x = pad_l + i * step + 3
        base = 0.0
        for j, (k, name) in enumerate(keys):
            v = float(r[k] or 0)
            top = y(base + v)
            hgt = y(base) - top
            # 2px surface gap between stacked segments
            out.append(
                f"<rect x='{x:.1f}' y='{top + 1:.1f}' width='{bw:.1f}' height='{max(0.5, hgt - 2):.1f}' fill='{SERIES[j]}'>"
                f"<title>{html.escape(str(r[label_key]))} {name}: {v:,.0f} USD m</title></rect>"
            )
            base += v
        out.append(
            f"<text x='{x + bw / 2:.1f}' y='{h - 8}' class='axis' text-anchor='middle'>{html.escape(str(r[label_key]))}</text>"
        )
    legend = "".join(
        f"<span class='key'><i style='background:{SERIES[j]}'></i>{html.escape(name)}</span>"
        for j, (_, name) in enumerate(keys)
    )
    return (
        f"<figure><figcaption>{html.escape(title)}</figcaption><div class='legend'>{legend}</div>"
        f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{html.escape(title)}'>{''.join(out)}</svg></figure>"
    )


def _table(rows: list[dict[str, Any]], cols: list[tuple[str, str, str]]) -> str:
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label, _ in cols)
    body = []
    for r in rows:
        cells = []
        for key, _, fmt in cols:
            v = r.get(key)
            if fmt == "pct":
                text = _pct(v)
            elif fmt == "share":
                text = _share(v)
            else:
                text = _fmt(v, 2 if fmt == "2" else 1)
            cells.append(f"<td>{text}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<details><summary>Table view</summary><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></details>"


def _tile(label: str, value: str, note: str = "") -> str:
    return f"<div class='tile'><div class='tile-label'>{html.escape(label)}</div><div class='tile-value'>{value}</div><div class='tile-note'>{html.escape(note)}</div></div>"


CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--grid:#e4e3df;--card:#ffffff;color-scheme:light}
@media (prefers-color-scheme: dark){:root{--surface:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;--grid:#383835;--card:#232322;color-scheme:dark}}
body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--surface);color:var(--ink)}
main{max-width:980px;margin:0 auto;padding:24px}
h1{font-size:26px;margin:8px 0}h2{font-size:19px;margin-top:36px;border-bottom:1px solid var(--grid);padding-bottom:4px}
.muted,.tile-note,figcaption,.axis{color:var(--ink2)}
.badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid var(--ink2);color:var(--ink2);margin-left:6px;vertical-align:middle}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:12px 0}
.tile{background:var(--card);border:1px solid var(--grid);border-radius:8px;padding:12px}
.tile-label{font-size:13px;color:var(--ink2)}.tile-value{font-size:24px;font-weight:600}.tile-note{font-size:12px}
figure{margin:12px 0;background:var(--card);border:1px solid var(--grid);border-radius:8px;padding:12px}
svg{width:100%;height:auto}.grid{stroke:var(--grid);stroke-width:1}.axis{font-size:11px;fill:var(--ink2)}
.legend{font-size:13px;margin:4px 0}.key{margin-right:14px}.key i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}th,td{text-align:right;padding:4px 8px;border-bottom:1px solid var(--grid)}th:first-child,td:first-child{text-align:left}
details{margin:6px 0 12px}code{font-size:13px}footer{margin-top:40px;font-size:12px;color:var(--ink2)}
"""


def build_site(settings: Settings, out_dir: Path) -> Path:
    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    sim = json.loads((settings.run_dir / "simulation_summary.json").read_text(encoding="utf-8"))
    upi = _q(con, "select * from marts.mart_upi_growth_monthly order by period_month")
    cards = _q(
        con,
        "select * from marts.mart_credit_card_spend_per_card where is_total_row order by period_month",
    )
    banks = _q(
        con,
        """select bank_name, credit_cards_outstanding, spend_per_card_inr, share_of_industry_spend
           from marts.mart_credit_card_spend_per_card
           where not is_total_row and period_month = (select max(period_month) from marts.mart_credit_card_spend_per_card)
           order by cc_spend_inr desc limit 10""",
    )
    hood = _q(
        con,
        "select * from marts.mart_robinhood_kpis where funded_customers_m is not null or total_revenue_musd is not null order by period_end",
    )
    book = _q(
        con,
        """select as_of_date, sum(market_value) mv, sum(daily_pnl) pnl, count(distinct customer_id) customers,
                  sum(open_positions) positions from marts.mart_daily_pnl group by 1 order by 1""",
    )
    settle = _q(con, "select * from marts.mart_settlement_cash order by trade_date")
    fraud = _q(con, "select * from marts.mart_fraud_rule_performance order by rule_name")
    calib = _q(con, "select * from marts.mart_sim_card_spend_vs_rbi")
    recon = _q(
        con,
        "select source, check_name, count(*) n, sum(case when status='OK' then 1 else 0 end) ok from marts.mart_ledger_reconciliation group by 1,2 order by 1,2",
    )
    con.close()

    lu, lc, lh = (
        upi[-1],
        cards[-1],
        next((r for r in reversed(hood) if r["funded_customers_m"]), hood[-1]),
    )
    recon_ok = all(r["n"] == r["ok"] for r in recon)
    built = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    sources = read_manifest(settings).get("sources", {})
    nse_syn = bool(sources.get("nse", {}).get("synthetic"))
    rbi_syn = bool(sources.get("rbi", {}).get("synthetic"))
    rbi_badge = "synthetic fixture · not RBI data" if rbi_syn else "real · RBI"
    rbi_note = "SYNTHETIC fixture, not RBI data" if rbi_syn else ""
    real = ["SEC EDGAR"] + ([] if nse_syn else ["NSE"]) + ([] if rbi_syn else ["RBI"])
    synthetic = (["NSE-layout prices"] if nse_syn else []) + (
        ["RBI-layout payment statistics"] if rbi_syn else []
    )
    intro = f"Built {built}. Real public data ({', '.join(real)})"
    if synthetic:
        intro += (
            f", <strong>synthetic</strong> {' and '.join(synthetic)} (committed test fixtures "
            "used in place of NSE/RBI downloads; see DATA_LICENSE.md for why)"
        )
    summary = {
        "built_at": built,
        "upi_latest_month": str(lu["period_month"]),
        "upi_volume_mn": round(lu["volume_mn"], 1),
        "upi_volume_yoy": round(lu["volume_yoy"], 4) if lu["volume_yoy"] is not None else None,
        "credit_cards_month": str(lc["period_month"]),
        "credit_cards_outstanding": int(lc["credit_cards_outstanding"]),
        "credit_card_spend_per_card_inr": round(lc["spend_per_card_inr"]),
        "hood_latest_quarter": str(lh["period_end"]),
        "hood_funded_customers_m": lh["funded_customers_m"],
        "hood_platform_assets_bn": lh["platform_assets_bn"],
        "sources": {
            "sec": "real",
            "nse": "synthetic" if nse_syn else "real",
            "rbi": "synthetic" if rbi_syn else "real",
        },
        "simulation_window": sim["window"],
        "simulated_postings": sim["ledger"].get("posted", 0),
        "simulated_rejections": sim["ledger"].get("rejected", 0),
        "idempotent_replays": sim["ledger"].get("replayed", 0),
        "reconciliation_ok": recon_ok,
    }

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Fintech broking + cards ledger on AWS (emulated): results</title>",
        f"<style>{CSS}</style></head><body><main>",
        "<h1>Broking + cards: event-driven ledger results</h1>",
        f"<p class='muted'>{intro} plus a <strong>simulated</strong>, seeded event stream "
        "run through an emulated AWS pipeline (DynamoDB, Streams, Lambda, Firehose, S3/Iceberg, dbt). No real AWS account is used.</p>",
        "<div class='tiles'>",
        _tile(
            "UPI transactions, " + lu["period_month"].strftime("%b %Y"),
            f"{lu['volume_mn'] / 1000:,.2f} bn",
            f"{_pct(lu['volume_yoy'])} YoY · {rbi_note or 'RBI PSI'}",
        ),
        _tile(
            "Credit cards outstanding, " + lc["period_month"].strftime("%b %Y"),
            f"{lc['credit_cards_outstanding'] / 1e6:,.1f} M",
            rbi_note or "RBI card statistics",
        ),
        _tile(
            "Credit-card spend per card",
            f"₹{lc['spend_per_card_inr']:,.0f}",
            lc["period_month"].strftime("%b %Y")
            + " · per month"
            + (f" · {rbi_note}" if rbi_syn else ""),
        ),
        _tile(
            "Robinhood funded customers",
            f"{lh['funded_customers_m']:.1f} M",
            f"{lh['period_end']} · SEC 10-Q/10-K",
        ),
        _tile(
            "Ledger reconciliation",
            "OK" if recon_ok else "BREAK",
            f"{sum(r['n'] for r in recon)} checks · simulated ledger",
        ),
        "</div>",
        f"<h2>UPI growth by month <span class='badge'>{rbi_badge}</span></h2>",
        _line_chart(
            [(r["period_month"].strftime("%b %y"), r["volume_mn"]) for r in upi],
            "UPI transactions per month (million)",
            "million",
        ),
        _table(
            list(reversed(upi)),
            [
                ("period_month", "Month", ""),
                ("volume_mn", "Volume (mn)", ""),
                ("value_crore", "Value (₹ crore)", ""),
                ("avg_ticket_inr", "Avg ticket (₹)", ""),
                ("volume_mom", "MoM", "pct"),
                ("volume_yoy", "YoY", "pct"),
            ],
        ),
        f"<h2>Credit-card spend per card <span class='badge'>{rbi_badge}</span></h2>",
        _line_chart(
            [(r["period_month"].strftime("%b %y"), r["spend_per_card_inr"]) for r in cards],
            "Spend per credit card per month, all banks (₹)",
            "₹",
        ),
        f"<p class='muted'>Top issuers by spend, {lc['period_month']:%B %Y}:</p>",
        _table(
            banks,
            [
                ("bank_name", "Bank", ""),
                ("credit_cards_outstanding", "Cards", ""),
                ("spend_per_card_inr", "Spend/card (₹)", ""),
                ("share_of_industry_spend", "Share of spend", "share"),
            ],
        ).replace("<details>", "<details open>"),
        "<h2>Robinhood KPI trends <span class='badge'>real · SEC EDGAR</span></h2>",
        _line_chart(
            [
                (str(r["period_end"])[:7], r["funded_customers_m"])
                for r in hood
                if r["funded_customers_m"]
            ],
            "Funded customers (million)",
            "million",
        ),
        _stacked_bars(
            [r for r in hood if r["total_revenue_musd"] and r["revenue_parts_reconcile"]][-10:],
            [
                ("transaction_revenue_musd", "Transaction-based"),
                ("net_interest_revenue_musd", "Net interest"),
                ("other_revenue_musd", "Other"),
            ],
            "period_end",
            "Revenue by type, USD million per quarter (Q4 derived from the 10-K)",
        ),
        _table(
            list(reversed(hood)),
            [
                ("period_end", "Quarter end", ""),
                ("funded_customers_m", "Funded cust. (M)", ""),
                ("platform_assets_bn", "Platform assets ($bn)", ""),
                ("net_deposits_bn", "Net deposits ($bn)", ""),
                ("total_revenue_musd", "Revenue ($m)", ""),
                ("revenue_yoy", "Revenue YoY", "pct"),
            ],
        ),
        "<h2>Simulated broking book <span class='badge'>simulated · "
        + ("priced at synthetic prices, not NSE data" if nse_syn else "priced at real NSE closes")
        + "</span></h2>",
        f"<p class='muted'>{sim['customers']} simulated customers trading {sim['universe']} "
        + (
            "synthetic stocks (NSE bhavcopy layout, no NSE data), "
            if nse_syn
            else "of the most-traded NSE stocks, "
        )
        + f"{sim['window'][0]} to {sim['window'][1]} ({sim['trading_days']} sessions). Fills at the "
        + ("synthetic" if nse_syn else "real")
        + " daily VWAP; brokerage is an assumption.</p>",
        _line_chart(
            [(str(r["as_of_date"])[5:], float(r["mv"])) for r in book],
            "Book market value at the close (₹)",
            "₹",
        ),
        _line_chart(
            [(str(r["as_of_date"])[5:], float(r["pnl"])) for r in book],
            "Book daily P&L (₹)",
            "₹",
            kind="bar",
        ),
        _table(
            book,
            [
                ("as_of_date", "Session", ""),
                ("customers", "Customers", ""),
                ("positions", "Open positions", ""),
                ("mv", "Market value (₹)", ""),
                ("pnl", "Daily P&L (₹)", ""),
            ],
        ),
        "<h2>T+1 settlement cash <span class='badge'>simulated</span></h2>",
        _table(
            settle,
            [
                ("trade_date", "Trade date", ""),
                ("settlement_date", "Settles", ""),
                ("trades", "Trades", ""),
                ("pay_in_obligation", "Pay-in (₹)", ""),
                ("pay_out_receivable", "Pay-out (₹)", ""),
                ("net_pay_in", "Net (₹)", ""),
                ("settlement_status", "Status", ""),
            ],
        ).replace("<details>", "<details open>"),
        "<h2>Card fraud rules <span class='badge'>simulated labels</span></h2>",
        "<p class='muted'>Frauds are injected by the simulator (velocity bursts, impossible travel, amount spikes, night-time risky merchants); the rules never see the labels.</p>",
        _table(
            fraud,
            [
                ("rule_name", "Rule", ""),
                ("alerts", "Alerts", ""),
                ("true_positives", "True positives", ""),
                ("labelled_frauds", "Labelled frauds", ""),
                ("precision", "Precision", "share"),
                ("recall", "Recall", "share"),
            ],
        ).replace("<details>", "<details open>"),
        "<h2>Calibration: simulated card spend vs RBI <span class='badge'>"
        + ("simulated vs synthetic fixture" if rbi_syn else "simulated vs real")
        + "</span></h2>",
        _table(
            calib,
            [
                ("rbi_month", "RBI month", ""),
                ("sim_spend_per_card_day", "Sim spend/card/day (₹)", ""),
                ("rbi_spend_per_card_day", "RBI spend/card/day (₹)", ""),
                ("sim_txns_per_card_day", "Sim txns/card/day", "2"),
                ("rbi_txns_per_card_day", "RBI txns/card/day", "2"),
                ("sim_avg_ticket_inr", "Sim avg ticket (₹)", ""),
                ("rbi_avg_ticket_inr", "RBI avg ticket (₹)", ""),
            ],
        ).replace("<details>", "<details open>"),
        "<h2>Ledger reconciliation <span class='badge'>simulated ledger</span></h2>",
        _table(
            recon,
            [
                ("source", "Source", ""),
                ("check_name", "Check", ""),
                ("n", "Rows", ""),
                ("ok", "OK", ""),
            ],
        ).replace("<details>", "<details open>"),
        f"<p class='muted'>Event pipeline counters: {sim['ledger'].get('posted', 0):,} postings, {sim['ledger'].get('rejected', 0):,} rejected by ledger constraints, "
        f"{sim['ledger'].get('replayed', 0):,} idempotent replays, {sim['ledger'].get('conflict', 0):,} key conflicts; "
        f"{sim['stream'].get('redelivered_records', 0):,} stream records redelivered and de-duplicated in Iceberg.</p>",
        "<footer>Sources: U.S. SEC EDGAR (public domain); "
        + (
            "RBI-layout payment statistics: synthetic test fixtures, not RBI data; "
            if rbi_syn
            else "Reserve Bank of India, Payment System Indicators and ATM/POS/Card Statistics "
            "(derived figures, with attribution; see DATA_LICENSE.md); "
        )
        + (
            "share prices: synthetic test fixtures in the NSE bhavcopy layout, not NSE data. "
            if nse_syn
            else "National Stock Exchange of India bhavcopy, used only to price simulated trades "
            "(not redistributed). "
        )
        + "Customer orders, card transactions, balances and fraud labels are simulated. "
        "Code: MIT. Not investment advice; no affiliation with any company named.</footer>",
        "</main></body></html>",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text("".join(parts), encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out_dir / "index.html"
