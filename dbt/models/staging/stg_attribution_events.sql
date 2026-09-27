with src as ({{ dedup_first_arrival(source('raw', 'attribution_events')) }})
select event_id as attribution_event_id, player_id, attributed_at, channel,
       coalesce(affiliate_id, 'none') as affiliate_id, reason, _ingested_at,
       {{ is_late('attributed_at') }} as is_late
from src
