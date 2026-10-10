-- SIMULATED positions marked to market at the raw_nse_bhavcopy closing prices (SYNTHETIC by
-- default), one row per customer, ISIN and session.
-- daily_pnl = change in market value - net cash paid for the day's trades - fees.
with flows as (
    select
        trade_date,
        customer_id,
        isin,
        max(symbol) as symbol,
        sum(case when side = 'BUY' then qty else -qty end) as qty_traded,
        sum(case when side = 'BUY' then notional else -notional end) as net_cash_out,
        sum(fees) as fees
    from {{ ref('int_trade_fills') }}
    group by trade_date, customer_id, isin
),

pairs as (
    select
        customer_id,
        isin,
        max(symbol) as symbol,
        min(trade_date) as first_trade_date
    from flows
    group by customer_id, isin
),

grid as (
    select
        d.trade_date as as_of_date,
        p.customer_id,
        p.isin,
        p.symbol
    from {{ ref('int_trading_days') }} as d
    inner join pairs as p on d.trade_date >= p.first_trade_date
),

positions as (
    select
        g.as_of_date,
        g.customer_id,
        g.isin,
        g.symbol,
        coalesce(f.qty_traded, 0) as qty_traded,
        coalesce(f.net_cash_out, 0) as net_cash_out,
        coalesce(f.fees, 0) as fees,
        sum(coalesce(f.qty_traded, 0)) over (
            partition by g.customer_id, g.isin
            order by g.as_of_date
            rows between unbounded preceding and current row
        ) as quantity
    from grid as g
    left join flows as f
        on
            g.as_of_date = f.trade_date
            and g.customer_id = f.customer_id
            and g.isin = f.isin
),

valued as (
    select
        p.as_of_date,
        p.customer_id,
        p.isin,
        p.symbol,
        p.qty_traded,
        p.net_cash_out,
        p.fees,
        p.quantity,
        px.close_price,
        cast(p.quantity * px.close_price as decimal(24, 4)) as market_value
    from positions as p
    inner join {{ ref('stg_nse_prices') }} as px
        on p.as_of_date = px.trade_date and p.isin = px.isin
),

with_pnl as (
    select
        *,
        market_value
        - lag(market_value, 1, 0) over (partition by customer_id, isin order by as_of_date)
        - net_cash_out
        - fees as daily_pnl
    from valued
)

select
    as_of_date,
    customer_id,
    isin,
    symbol,
    quantity,
    qty_traded,
    close_price,
    market_value,
    net_cash_out,
    fees,
    cast(daily_pnl as decimal(24, 4)) as daily_pnl,
    true as simulated
from with_pnl
where quantity <> 0 or qty_traded <> 0
