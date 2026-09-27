with src as ({{ dedup_first_arrival(source('raw', 'bonus_events')) }})
select
    s.event_id as bonus_event_id,
    s.bonus_id,
    s.player_id,
    s.event_ts,
    cast(s.event_ts as date) as event_date,
    s.event_type,
    s.bonus_type,
    cast(s.amount as decimal(18, 2)) as amount,
    s.currency,
    round(s.amount * fx.eur_per_unit, 4) as amount_eur,
    s.wagering_requirement,
    s.campaign_id,
    s._ingested_at
from src s
left join {{ ref('fx_rates') }} fx
  on fx.currency = s.currency and fx.month = date_trunc('month', s.event_ts)
