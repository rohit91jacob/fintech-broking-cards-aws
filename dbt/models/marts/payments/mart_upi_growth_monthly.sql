-- SYNTHETIC by default (raw_rbi_psi). UPI volume and value by month (RBI Payment System
-- Indicators layout), with growth rates.
with upi as (
    select
        period_month,
        max(case when measure = 'volume_lakh' then value end) / 10.0 as volume_mn,
        max(case when measure = 'value_crore' then value end) as value_crore
    from {{ ref('stg_rbi_psi') }}
    where metric = 'upi'
    group by period_month
)

select
    cur.period_month,
    cur.volume_mn,
    cur.value_crore,
    cur.value_crore * 1e7 / nullif(cur.volume_mn * 1e6, 0) as avg_ticket_inr,
    cur.volume_mn / nullif(lag(cur.volume_mn) over (order by cur.period_month), 0) - 1 as volume_mom,
    cur.volume_mn / nullif(prev.volume_mn, 0) - 1 as volume_yoy,
    cur.value_crore / nullif(prev.value_crore, 0) - 1 as value_yoy
from upi as cur
left join upi as prev
    on prev.period_month = {{ dbt.dateadd('month', -12, 'cur.period_month') }}
