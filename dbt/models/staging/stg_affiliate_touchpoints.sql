with src as ({{ dedup_first_arrival(source('raw', 'affiliate_touchpoints')) }})
select event_id as touchpoint_id, player_id, touch_ts, channel, affiliate_id, campaign, _ingested_at
from src
