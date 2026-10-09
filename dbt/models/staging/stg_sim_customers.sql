-- SIMULATED broking customers.
select customer_id, city, risk_profile, onboarded_on
from {{ source('lake', 'sim_customers') }}
