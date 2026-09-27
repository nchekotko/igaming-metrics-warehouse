with src as ({{ dedup_first_arrival(source('raw', 'rg_events')) }})
select event_id as rg_event_id, player_id, event_ts, cast(event_ts as date) as event_date,
       event_type, limit_amount, limit_period, _ingested_at
from src
