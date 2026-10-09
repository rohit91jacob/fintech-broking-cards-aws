{{ config(severity='warn') }}
-- Two RBI publications agree: credit cards outstanding in the bank-wise card statistics
-- (all banks) equals the Payment System Indicators figure for the same month within 0.5 %.
select period_month, credit_cards_outstanding, psi_credit_cards_outstanding
from {{ ref('mart_credit_card_spend_per_card') }}
where
    is_total_row
    and psi_credit_cards_outstanding is not null
    and abs(credit_cards_outstanding - psi_credit_cards_outstanding) > 0.005 * psi_credit_cards_outstanding
