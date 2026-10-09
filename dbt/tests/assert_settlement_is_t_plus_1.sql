-- Every settled trade settled on the next NSE session after its trade date, and the
-- settlement date the API recorded is that session (when it lies inside the window).
select trade_txn_id, f.trade_date, f.settlement_date, f.settled_on, t.next_trade_date
from {{ ref('int_trade_fills') }} as f
inner join {{ ref('int_trading_days') }} as t on f.trade_date = t.trade_date
where
    (f.is_settled and f.settled_on <> f.settlement_date)
    or (t.next_trade_date is not null and f.settlement_date <> t.next_trade_date)
    or (t.next_trade_date is not null and not f.is_settled)
