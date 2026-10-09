-- Double entry: the legs of every posted transaction sum to zero for each unit.
select txn_id, unit, sum(amount) as total
from {{ ref('stg_ledger_entries') }}
group by txn_id, unit
having sum(amount) <> 0
