-- The latest reported quarter's revenue types add up to total revenue.
select *
from {{ ref('mart_robinhood_kpis') }}
where
    period_end = (
        select max(period_end) from {{ ref('mart_robinhood_kpis') }} where total_revenue_musd is not null
    )
    and not revenue_parts_reconcile
