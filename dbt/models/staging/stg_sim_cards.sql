-- SIMULATED card master.
select card_id, customer_id, issuer_bank, home_city, cast(credit_limit as decimal(20, 2)) as credit_limit, statement_day
from {{ source('lake', 'sim_cards') }}
