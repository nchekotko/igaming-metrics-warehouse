-- Bet STATE CHANGES (open, then settled). Deduplicated per delivery; collapsed per bet in int_bets.
with src as ({{ dedup_first_arrival(source('raw', 'bets')) }})
select
    s.event_id as bet_event_id,
    s.bet_id,
    s.player_id,
    s.event_ts,
    s.placed_at,
    s.settled_at,
    s.status,
    s.bet_type,
    cast(s.stake as decimal(18, 2)) as stake,
    cast(s.total_odds as decimal(18, 2)) as total_odds,
    cast(s.payout as decimal(18, 2)) as payout,
    s.currency,
    s._ingested_at,
    {{ is_late('s.event_ts', 's._ingested_at') }} as is_late
from src s
