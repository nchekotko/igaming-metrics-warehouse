{#
  Incremental on ARRIVAL time (_ingested_at), not event time: a round that happened in March but
  arrived in April is still picked up by the April run. Filtering on round_ts would silently drop
  late events — the classic late-arrival bug.
#}
{{
    config(
        materialized='incremental',
        unique_key='round_id',
        incremental_strategy='delete+insert',
        on_schema_change='fail'
    )
}}
select
    round_id,
    player_id,
    round_ts,
    round_date,
    provider_id,
    game_id,
    game_type,
    currency,
    stake,
    win,
    stake_eur,
    win_eur,
    stake_eur - win_eur as ggr_eur,
    case when stake > 0 then win / stake end as win_multiplier,
    tz_adjusted,
    is_late,
    _ingested_at
from {{ ref('stg_game_rounds') }}
{% if is_incremental() %}
where _ingested_at > (select max(_ingested_at) from {{ this }})
{% endif %}
