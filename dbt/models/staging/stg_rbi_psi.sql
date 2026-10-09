-- REAL. Each month appears in up to three releases (as "latest", "previous" and
-- "same month last year"); keep the value from the most recent release.
with ranked as (
    select
        *,
        row_number() over (
            partition by period_month, metric, measure order by release_month desc
        ) as rn
    from {{ source('lake', 'raw_rbi_psi') }}
)

select period_month, metric, measure, value, release_month, source_file
from ranked
where rn = 1
