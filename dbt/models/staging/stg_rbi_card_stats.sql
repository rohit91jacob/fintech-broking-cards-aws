-- REAL. Bank-wise credit card statistics; values converted from Rs '000 to rupees.
select
    period_month,
    bank_group,
    bank_name,
    is_total_row,
    credit_cards_outstanding,
    debit_cards_outstanding,
    cc_pos_volume,
    cc_pos_value_thousand * 1000 as cc_pos_value_inr,
    cc_ecom_volume,
    cc_ecom_value_thousand * 1000 as cc_ecom_value_inr,
    coalesce(cc_others_volume, 0) as cc_others_volume,
    coalesce(cc_others_value_thousand, 0) * 1000 as cc_others_value_inr,
    source_file
from {{ source('lake', 'raw_rbi_card_stats') }}
