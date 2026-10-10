-- SIMULATED customer-level daily P&L, marked to market at the raw_nse_bhavcopy closes
-- (SYNTHETIC by default).
with by_customer as (
    select
        as_of_date,
        customer_id,
        count(case when quantity <> 0 then 1 end) as open_positions,
        sum(market_value) as market_value,
        sum(net_cash_out) as net_cash_out,
        sum(fees) as fees,
        sum(daily_pnl) as daily_pnl
    from {{ ref('fct_positions_mtm') }}
    group by as_of_date, customer_id
)

select
    as_of_date,
    customer_id,
    open_positions,
    market_value,
    net_cash_out,
    fees,
    daily_pnl,
    sum(daily_pnl) over (
        partition by customer_id
        order by as_of_date
        rows between unbounded preceding and current row
    ) as cumulative_pnl,
    true as simulated
from by_customer
