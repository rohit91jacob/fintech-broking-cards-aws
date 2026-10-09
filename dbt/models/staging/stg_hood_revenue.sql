-- REAL. One value per (period, revenue type); later filings win (they carry restatements).
with ranked as (
    select
        *,
        date_diff('day', period_start, period_end) as period_days,
        row_number() over (
            partition by period_start, period_end, revenue_type order by report_date desc
        ) as rn
    from {{ source('lake', 'raw_hood_revenue_by_type') }}
)

select
    period_start,
    period_end,
    case when period_days between 80 and 100 then 3 when period_days between 360 and 370 then 12 end as period_months,
    revenue_type,
    value_usd,
    accession,
    form
from ranked
where rn = 1 and (period_days between 80 and 100 or period_days between 360 and 370)
