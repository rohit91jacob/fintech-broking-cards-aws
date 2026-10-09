select run_id, checked_at, check_name, scope, expected, actual, difference, status
from {{ source('lake', 'recon_results') }}
