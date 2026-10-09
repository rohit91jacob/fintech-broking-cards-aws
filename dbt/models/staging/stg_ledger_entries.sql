-- SIMULATED ledger legs. account = '<owner>:<kind>', e.g. 'CUST#C00001:CASH'.
select
    txn_id,
    leg_no,
    txn_type,
    business_date,
    event_ts,
    account,
    split_part(account, ':', 1) as account_owner,
    split_part(account, ':', 2) as account_kind,
    case
        when account like 'CUST#%' then substr(split_part(account, ':', 1), 6)
    end as customer_id,
    case
        when account like 'CARD#%' then substr(split_part(account, ':', 1), 6)
    end as card_id,
    unit,
    cast(amount as decimal(20, 4)) as amount,
    idempotency_key,
    simulated
from {{ source('lake', 'ledger_entries') }}
