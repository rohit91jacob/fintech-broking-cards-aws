-- SIMULATED card authorisations (approved and declined) scored by four rules.
-- injected_fraud / fraud_scenario are the simulator's ground-truth labels, used only to
-- measure the rules (mart_fraud_rule_performance); the rules never read them.
with auths as (
    select
        txn_id,
        card_id,
        customer_id,
        business_date,
        event_ts,
        status,
        amount,
        channel,
        mcc,
        merchant_category,
        merchant_city,
        injected_fraud,
        fraud_scenario
    from {{ ref('stg_business_events') }}
    where txn_type = 'CARD_AUTH'
),

velocity as (
    select
        txn_id,
        count(*) over (
            partition by card_id
            order by event_ts
            range between interval '{{ var("fraud_velocity_minutes") }}' minute preceding and current row
        ) as auths_in_window
    from auths
),

in_person as (
    select
        txn_id,
        merchant_city,
        event_ts,
        lag(merchant_city) over (partition by card_id order by event_ts) as prev_city,
        lag(event_ts) over (partition by card_id order by event_ts) as prev_ts
    from auths
    where channel = 'POS'
),

history as (
    select
        txn_id,
        avg(amount) over (
            partition by card_id
            order by event_ts
            rows between unbounded preceding and 1 preceding
        ) as prior_avg_amount,
        count(*) over (
            partition by card_id
            order by event_ts
            rows between unbounded preceding and 1 preceding
        ) as prior_auths
    from auths
),

scored as (
    select
        a.*,
        v.auths_in_window,
        p.prev_city,
        h.prior_avg_amount,
        v.auths_in_window >= {{ var('fraud_velocity_count') }} as rule_velocity,
        coalesce(
            p.prev_city is not null
            and p.prev_city <> a.merchant_city
            and date_diff('minute', p.prev_ts, a.event_ts) <= {{ var('fraud_travel_minutes') }},
            false
        ) as rule_impossible_travel,
        coalesce(
            h.prior_auths >= 1
            and a.amount >= {{ var('fraud_spike_min_inr') }}
            and a.amount > {{ var('fraud_spike_multiple') }} * h.prior_avg_amount,
            false
        ) as rule_amount_spike,
        a.mcc in ('7995', '6051') and hour(a.event_ts) < 5 as rule_night_risky_mcc
    from auths as a
    inner join velocity as v on a.txn_id = v.txn_id
    inner join history as h on a.txn_id = h.txn_id
    left join in_person as p on a.txn_id = p.txn_id
)

select
    txn_id,
    card_id,
    customer_id,
    business_date,
    event_ts,
    status,
    amount,
    channel,
    mcc,
    merchant_category,
    merchant_city,
    auths_in_window,
    prev_city,
    prior_avg_amount,
    rule_velocity,
    rule_impossible_travel,
    rule_amount_spike,
    rule_night_risky_mcc,
    rule_velocity or rule_impossible_travel or rule_amount_spike or rule_night_risky_mcc as is_alert,
    injected_fraud,
    fraud_scenario,
    true as simulated
from scored
