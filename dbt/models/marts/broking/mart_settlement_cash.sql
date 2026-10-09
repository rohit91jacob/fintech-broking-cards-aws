-- SIMULATED T+1 settlement obligations of the broker towards the clearing corporation.
-- pay_in: cash the broker owes for its customers' buys; pay_out: cash due to it for sells.
select
    f.trade_date,
    f.settlement_date,
    t.next_trade_date as expected_settlement_date,
    count(*) as trades,
    sum(case when f.side = 'BUY' then f.notional else 0 end) as pay_in_obligation,
    sum(case when f.side = 'SELL' then f.notional else 0 end) as pay_out_receivable,
    sum(case when f.side = 'BUY' then f.notional else -f.notional end) as net_pay_in,
    sum(f.fees) as brokerage_fees,
    sum(case when f.is_settled then 1 else 0 end) as settled_trades,
    min(f.settled_on) as settled_on,
    case
        when sum(case when f.is_settled then 1 else 0 end) = count(*) then 'SETTLED'
        when sum(case when f.is_settled then 1 else 0 end) = 0 then 'PENDING'
        else 'PARTIAL'
    end as settlement_status,
    true as simulated
from {{ ref('int_trade_fills') }} as f
left join {{ ref('int_trading_days') }} as t on f.trade_date = t.trade_date
group by f.trade_date, f.settlement_date, t.next_trade_date
