-- SIMULATED fills (posted trades) with their T+1 settlement outcome.
with fills as (
    select
        txn_id as trade_txn_id,
        business_date as trade_date,
        settlement_date,
        event_ts,
        customer_id,
        symbol,
        isin,
        side,
        qty,
        price,
        notional,
        fees
    from {{ ref('stg_business_events') }}
    where txn_type in ('TRADE_BUY', 'TRADE_SELL') and status = 'POSTED'
),

settlements as (
    select
        trade_txn_id,
        business_date as settled_on,
        txn_id as settlement_txn_id
    from {{ ref('stg_business_events') }}
    where txn_type in ('SETTLE_BUY', 'SETTLE_SELL') and status = 'POSTED'
)

select
    f.trade_txn_id,
    f.trade_date,
    f.settlement_date,
    f.event_ts,
    f.customer_id,
    f.symbol,
    f.isin,
    f.side,
    f.qty,
    f.price,
    f.notional,
    f.fees,
    s.settled_on,
    s.settlement_txn_id,
    s.settled_on is not null as is_settled
from fills as f
left join settlements as s on f.trade_txn_id = s.trade_txn_id
