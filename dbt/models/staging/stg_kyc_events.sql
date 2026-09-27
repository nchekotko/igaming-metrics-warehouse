with src as ({{ dedup_first_arrival(source('raw', 'kyc_events')) }})
select event_id, player_id, event_ts, status as kyc_status, document_type, _ingested_at,
       {{ is_late('event_ts') }} as is_late
from src
