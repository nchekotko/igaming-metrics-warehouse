-- round_ts is already UTC: the legacy provider's local Europe/Malta time was converted at ingestion
-- (contract `timezone_by_source`), flagged by tz_adjusted.
with src as ({{ dedup_first_arrival(source('raw', 'game_rounds')) }})
select
    s.event_id as round_id,
    s.player_id,
    s.round_ts,
    cast(s.round_ts as date) as round_date,
    s.provider_id,
    s.game_id,
    s.game_type,
    cast(s.stake as decimal(18, 2)) as stake,
    cast(s.win as decimal(18, 2)) as win,
    s.currency,
    round(s.stake * fx.eur_per_unit, 4) as stake_eur,
    round(s.win * fx.eur_per_unit, 4) as win_eur,
    s._ingested_at,
    s._tz_adjusted as tz_adjusted,
    {{ is_late('s.round_ts', 's._ingested_at') }} as is_late
from src s
left join {{ ref('fx_rates') }} fx
  on fx.currency = s.currency and fx.month = date_trunc('month', s.round_ts)
