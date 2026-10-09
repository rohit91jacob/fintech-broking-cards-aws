-- Reconciliation report: the Python job's cross-system checks (DynamoDB balances vs the
-- Iceberg ledger vs order and card events) plus independent checks recomputed here in SQL.
with lake_balances as (
    select
        account,
        unit,
        sum(amount) as balance
    from {{ ref('stg_ledger_entries') }}
    group by account, unit
),

pairs as (
    select
        coalesce(d.account, l.account) as account,
        coalesce(d.unit, l.unit) as unit,
        coalesce(d.balance, 0) as dynamo_balance,
        coalesce(l.balance, 0) as lake_balance
    from {{ ref('stg_ledger_balances') }} as d
    full outer join lake_balances as l on d.account = l.account and d.unit = l.unit
),

dbt_checks as (
    select
        'dbt_lake_vs_dynamodb' as check_name,
        cast(count(*) as varchar) || ' account/unit pairs' as scope,
        cast(0 as decimal(24, 4)) as expected,
        cast(sum(case when dynamo_balance <> lake_balance then 1 else 0 end) as decimal(24, 4)) as actual
    from pairs
    union all
    select
        'dbt_trial_balance' as check_name,
        unit as scope,
        cast(0 as decimal(24, 4)) as expected,
        cast(sum(lake_balance) as decimal(24, 4)) as actual
    from pairs
    group by unit
),

all_checks as (
    select
        'python_recon_job' as source,
        check_name,
        scope,
        cast(expected as decimal(24, 4)) as expected,
        cast(actual as decimal(24, 4)) as actual
    from {{ ref('stg_recon_results') }}
    union all
    select
        'dbt' as source,
        check_name,
        scope,
        expected,
        actual
    from dbt_checks
)

select
    source,
    check_name,
    scope,
    expected,
    actual,
    actual - expected as difference,
    case when actual = expected then 'OK' else 'BREAK' end as status
from all_checks
