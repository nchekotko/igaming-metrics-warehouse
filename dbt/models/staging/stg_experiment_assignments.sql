with src as ({{ dedup_first_arrival(source('raw', 'experiment_assignments')) }})
select player_id, experiment_id, arm, assigned_at, _ingested_at
from src
qualify row_number() over (partition by experiment_id, player_id order by assigned_at) = 1
