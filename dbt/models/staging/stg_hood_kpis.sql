-- REAL. Key Performance Metrics as reported in each 10-Q / 10-K.
select
    report_date as period_end,
    form,
    accession,
    funded_customers_m,
    platform_assets_bn,
    net_deposits_bn,
    net_deposits_period_months,
    arpu_usd,
    gold_subscribers_m
from {{ source('lake', 'raw_hood_kpis') }}
