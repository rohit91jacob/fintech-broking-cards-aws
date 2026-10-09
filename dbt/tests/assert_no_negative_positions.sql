-- The ledger forbids short selling, so no position can go below zero.
select *
from {{ ref('fct_positions_mtm') }}
where quantity < 0
