-- SIMULATED. Precision and recall of each rule against the simulator's injected-fraud labels.
with rules as (
    select txn_id, injected_fraud, 'velocity' as rule_name, rule_velocity as fired
    from {{ ref('fct_card_fraud_alerts') }}
    union all
    select txn_id, injected_fraud, 'impossible_travel' as rule_name, rule_impossible_travel as fired
    from {{ ref('fct_card_fraud_alerts') }}
    union all
    select txn_id, injected_fraud, 'amount_spike' as rule_name, rule_amount_spike as fired
    from {{ ref('fct_card_fraud_alerts') }}
    union all
    select txn_id, injected_fraud, 'night_risky_mcc' as rule_name, rule_night_risky_mcc as fired
    from {{ ref('fct_card_fraud_alerts') }}
    union all
    select txn_id, injected_fraud, 'any_rule' as rule_name, is_alert as fired
    from {{ ref('fct_card_fraud_alerts') }}
),

counts as (
    select
        rule_name,
        count(*) as authorisations,
        sum(case when fired then 1 else 0 end) as alerts,
        sum(case when fired and injected_fraud then 1 else 0 end) as true_positives,
        sum(case when injected_fraud then 1 else 0 end) as labelled_frauds
    from rules
    group by rule_name
)

select
    rule_name,
    authorisations,
    alerts,
    true_positives,
    labelled_frauds,
    cast(true_positives as double) / nullif(alerts, 0) as precision,
    cast(true_positives as double) / nullif(labelled_frauds, 0) as recall,
    cast(alerts as double) / nullif(authorisations, 0) as alert_rate,
    true as simulated
from counts
