-- Positions rebuilt from trade events on the last session equal SEC + SEC_PENDING balances
-- in the DynamoDB ledger snapshot.
with last_positions as (
    select customer_id, isin, quantity
    from {{ ref('fct_positions_mtm') }}
    where as_of_date = (select max(as_of_date) from {{ ref('fct_positions_mtm') }})
),

ledger as (
    select
        substr(split_part(account, ':', 1), 6) as customer_id,
        unit as isin,
        sum(balance) as quantity
    from {{ ref('stg_ledger_balances') }}
    where account like 'CUST#%' and account_kind in ('SEC', 'SEC_PENDING')
    group by substr(split_part(account, ':', 1), 6), unit
)

select
    coalesce(p.customer_id, l.customer_id) as customer_id,
    coalesce(p.isin, l.isin) as isin,
    p.quantity as from_events,
    l.quantity as from_ledger
from last_positions as p
full outer join ledger as l on p.customer_id = l.customer_id and p.isin = l.isin
where coalesce(p.quantity, 0) <> coalesce(l.quantity, 0)
