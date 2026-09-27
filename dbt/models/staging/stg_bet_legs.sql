with src as ({{ dedup_first_arrival(source('raw', 'bet_legs')) }})
select event_id as leg_event_id, bet_id, leg_no, event_ts, sport, market,
       cast(odds as decimal(18, 2)) as odds, leg_result, _ingested_at
from src
