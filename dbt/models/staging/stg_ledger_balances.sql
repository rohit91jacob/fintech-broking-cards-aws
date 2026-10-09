-- SIMULATED. DynamoDB balances table as scanned at the end of the latest run.
select
    run_id,
    snapshot_at,
    account,
    split_part(account, ':', 2) as account_kind,
    unit,
    cast(balance as decimal(24, 4)) as balance,
    entry_count
from {{ source('lake', 'ledger_balances_snapshot') }}
