{#
  SCD2 history of player attribution (channel / affiliate).

  `imw load` runs `dbt snapshot` after every monthly arrival batch, so this table records what
  the warehouse KNEW at each load. Timestamp strategy: dbt_valid_from = attributed_at of the
  decision, so late postbacks / disputes / fraud reversals get event-time validity windows.
  Limitation: two changes for the same player inside one batch collapse into the latest one.
#}
{% snapshot snap_player_attribution %}
{{
    config(
        unique_key='player_id',
        strategy='timestamp',
        updated_at='attributed_at',
        dbt_valid_to_current="cast('9999-12-31 00:00:00' as timestamp)"
    )
}}
select
    player_id,
    channel,
    coalesce(affiliate_id, 'none') as affiliate_id,
    reason,
    attributed_at,
    event_id as attribution_event_id
from {{ source('raw', 'attribution_events') }}
qualify row_number() over (
    partition by player_id order by attributed_at desc, _ingested_at desc, event_id desc
) = 1
{% endsnapshot %}
