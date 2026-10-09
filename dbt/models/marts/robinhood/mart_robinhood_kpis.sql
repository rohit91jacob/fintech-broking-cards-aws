-- REAL. Robinhood quarterly KPIs and revenue by type from SEC filings.
-- 10-Ks report full-year flows, so Q4 = full year - (Q1 + Q2 + Q3) for revenues and net deposits.
with rev_wide as (
    select
        period_end,
        period_months,
        max(case when revenue_type = 'total' then value_usd end) as total_revenue_usd,
        max(case when revenue_type = 'transaction_based' then value_usd end) as transaction_revenue_usd,
        max(case when revenue_type = 'net_interest' then value_usd end) as net_interest_revenue_usd,
        max(case when revenue_type = 'other' then value_usd end) as other_revenue_usd
    from {{ ref('stg_hood_revenue') }}
    group by period_end, period_months
),

quarters_reported as (
    select
        period_end,
        total_revenue_usd,
        transaction_revenue_usd,
        net_interest_revenue_usd,
        other_revenue_usd,
        false as derived_q4
    from rev_wide
    where period_months = 3
),

q4_derived as (
    select
        fy.period_end,
        fy.total_revenue_usd - sum(q.total_revenue_usd) as total_revenue_usd,
        fy.transaction_revenue_usd - sum(q.transaction_revenue_usd) as transaction_revenue_usd,
        fy.net_interest_revenue_usd - sum(q.net_interest_revenue_usd) as net_interest_revenue_usd,
        fy.other_revenue_usd - sum(q.other_revenue_usd) as other_revenue_usd,
        true as derived_q4
    from rev_wide as fy
    inner join quarters_reported as q
        on
            q.period_end > {{ dbt.dateadd('month', -12, 'fy.period_end') }}
            and q.period_end < fy.period_end
    where fy.period_months = 12
    group by
        fy.period_end,
        fy.total_revenue_usd,
        fy.transaction_revenue_usd,
        fy.net_interest_revenue_usd,
        fy.other_revenue_usd
    having count(*) = 3
),

revenue as (
    select * from quarters_reported
    union all
    select * from q4_derived
),

kpi_q as (
    select
        period_end,
        funded_customers_m,
        platform_assets_bn,
        net_deposits_bn,
        arpu_usd,
        gold_subscribers_m,
        false as derived_net_deposits
    from {{ ref('stg_hood_kpis') }}
    where net_deposits_period_months = 3
),

kpi_q4 as (
    select
        k.period_end,
        k.funded_customers_m,
        k.platform_assets_bn,
        k.net_deposits_bn - sum(q.net_deposits_bn) as net_deposits_bn,
        cast(null as double) as arpu_usd,
        k.gold_subscribers_m,
        true as derived_net_deposits
    from {{ ref('stg_hood_kpis') }} as k
    inner join kpi_q as q
        on
            q.period_end > {{ dbt.dateadd('month', -12, 'k.period_end') }}
            and q.period_end < k.period_end
    where k.net_deposits_period_months = 12
    group by
        k.period_end,
        k.funded_customers_m,
        k.platform_assets_bn,
        k.net_deposits_bn,
        k.gold_subscribers_m
    having count(*) = 3
),

kpis as (
    select * from kpi_q
    union all
    select * from kpi_q4
),

joined as (
    select
        coalesce(k.period_end, r.period_end) as period_end,
        k.funded_customers_m,
        k.platform_assets_bn,
        k.net_deposits_bn,
        k.arpu_usd,
        k.gold_subscribers_m,
        r.total_revenue_usd / 1e6 as total_revenue_musd,
        r.transaction_revenue_usd / 1e6 as transaction_revenue_musd,
        r.net_interest_revenue_usd / 1e6 as net_interest_revenue_musd,
        r.other_revenue_usd / 1e6 as other_revenue_musd,
        abs(
            r.total_revenue_usd
            - (r.transaction_revenue_usd + r.net_interest_revenue_usd + r.other_revenue_usd)
        ) < 1e6 as revenue_parts_reconcile,
        coalesce(r.derived_q4, false) or coalesce(k.derived_net_deposits, false) as includes_derived_q4
    from kpis as k
    full outer join revenue as r on k.period_end = r.period_end
),

indexed as (
    select
        *,
        cast(extract(year from period_end) * 4 + extract(quarter from period_end) as integer) as quarter_index
    from joined
)

-- Growth rates join on an explicit quarter index, so gaps in the filing history give NULL
-- rather than comparing against the wrong quarter.
select
    cur.period_end,
    cur.funded_customers_m,
    cur.platform_assets_bn,
    cur.net_deposits_bn,
    cur.arpu_usd,
    cur.gold_subscribers_m,
    cur.total_revenue_musd,
    cur.transaction_revenue_musd,
    cur.net_interest_revenue_musd,
    cur.other_revenue_musd,
    cur.revenue_parts_reconcile,
    cur.includes_derived_q4,
    cur.funded_customers_m / nullif(ly.funded_customers_m, 0) - 1 as funded_customers_yoy,
    cur.total_revenue_musd / nullif(ly.total_revenue_musd, 0) - 1 as revenue_yoy,
    cur.net_deposits_bn * 4 / nullif(pq.platform_assets_bn, 0) as annualized_net_deposit_rate
from indexed as cur
left join indexed as ly on cur.quarter_index - 4 = ly.quarter_index
left join indexed as pq on cur.quarter_index - 1 = pq.quarter_index
