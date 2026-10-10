-- Sessions inside the simulated window, with the next session (T+1): the trade dates of
-- raw_nse_bhavcopy (SYNTHETIC by default), which the raw gate checks against NSE's calendar.
with days as (
    select distinct trade_date from {{ ref('stg_nse_prices') }}
),

sessions as (
    select
        trade_date,
        lead(trade_date) over (order by trade_date) as next_trade_date
    from days
),

window_bounds as (
    select
        min(business_date) as first_day,
        max(business_date) as last_day
    from {{ ref('stg_business_events') }}
    where txn_type like 'TRADE_%'
)

select
    s.trade_date,
    s.next_trade_date
from sessions as s
cross join window_bounds as w
where s.trade_date between w.first_day and w.last_day
