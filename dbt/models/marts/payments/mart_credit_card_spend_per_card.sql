-- REAL. Credit-card spend per card outstanding, by issuing bank and month (RBI card statistics).
-- Spend = PoS + e-commerce + other (mail/telephone order) transactions; ATM cash is excluded.
with base as (
    select
        period_month,
        is_total_row,
        credit_cards_outstanding,
        cc_ecom_value_inr,
        case when is_total_row then 'ALL BANKS' else bank_name end as bank_name,
        coalesce(bank_group, 'All') as bank_group,
        cc_pos_volume + cc_ecom_volume + cc_others_volume as cc_txn_volume,
        cc_pos_value_inr + cc_ecom_value_inr + cc_others_value_inr as cc_spend_inr
    from {{ ref('stg_rbi_card_stats') }}
    where credit_cards_outstanding > 0 or is_total_row
),

totals as (
    select
        period_month,
        cc_spend_inr as total_spend_inr
    from base
    where is_total_row
),

psi_cards as (
    select
        period_month,
        value * 1e5 as psi_credit_cards_outstanding
    from {{ ref('stg_rbi_psi') }}
    where metric = 'credit_cards_outstanding'
)

select
    b.period_month,
    b.bank_name,
    b.bank_group,
    b.is_total_row,
    b.credit_cards_outstanding,
    b.cc_txn_volume,
    b.cc_spend_inr,
    b.cc_spend_inr / nullif(b.credit_cards_outstanding, 0) as spend_per_card_inr,
    b.cc_txn_volume / nullif(b.credit_cards_outstanding, 0) as txns_per_card,
    b.cc_spend_inr / nullif(b.cc_txn_volume, 0) as avg_ticket_inr,
    b.cc_ecom_value_inr / nullif(b.cc_spend_inr, 0) as ecom_share_of_spend,
    b.cc_spend_inr / nullif(t.total_spend_inr, 0) as share_of_industry_spend,
    case when b.is_total_row then p.psi_credit_cards_outstanding end as psi_credit_cards_outstanding
from base as b
left join totals as t on b.period_month = t.period_month
left join psi_cards as p on b.period_month = p.period_month
