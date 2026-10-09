-- Calibration check: SIMULATED card spend per card per day against the REAL RBI average for
-- the month the simulator was calibrated on (the latest month in the card statistics).
with rbi as (
    select
        period_month,
        cc_spend_inr / credit_cards_outstanding as rbi_spend_per_card_month,
        cc_txn_volume / credit_cards_outstanding as rbi_txns_per_card_month,
        cc_spend_inr / cc_txn_volume as rbi_avg_ticket_inr,
        date_diff('day', period_month, {{ dbt.dateadd('month', 1, 'period_month') }}) as days_in_month
    from {{ ref('mart_credit_card_spend_per_card') }}
    where
        is_total_row
        and period_month = (select max(period_month) from {{ ref('mart_credit_card_spend_per_card') }})
),

sim as (
    select
        count(distinct business_date) as days,
        count(*) as approved_auths,
        sum(amount) as approved_spend_inr
    from {{ ref('stg_business_events') }}
    where txn_type = 'CARD_AUTH' and status = 'POSTED' and not injected_fraud
),

cards as (
    select count(*) as cards from {{ ref('stg_sim_cards') }}
)

select
    r.period_month as rbi_month,
    c.cards as simulated_cards,
    s.days as simulated_days,
    s.approved_spend_inr / nullif(c.cards * s.days, 0) as sim_spend_per_card_day,
    r.rbi_spend_per_card_month / r.days_in_month as rbi_spend_per_card_day,
    cast(s.approved_auths as double) / nullif(c.cards * s.days, 0) as sim_txns_per_card_day,
    r.rbi_txns_per_card_month / r.days_in_month as rbi_txns_per_card_day,
    s.approved_spend_inr / nullif(s.approved_auths, 0) as sim_avg_ticket_inr,
    r.rbi_avg_ticket_inr
from rbi as r
cross join sim as s
cross join cards as c
