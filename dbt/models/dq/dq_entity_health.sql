-- Per-entity ingestion health: volume, duplicate rate, late-arrival rate, quarantine rate,
-- timezone normalisation share, event-time freshness vs var('data_as_of').
{% set entities = {
    'players': 'registered_at',
    'kyc_events': 'event_ts',
    'affiliate_touchpoints': 'touch_ts',
    'attribution_events': 'attributed_at',
    'wallet_transactions': 'tx_ts',
    'bets': 'event_ts',
    'bet_legs': 'event_ts',
    'game_rounds': 'round_ts',
    'bonus_events': 'event_ts',
    'rg_events': 'event_ts',
    'experiment_assignments': 'assigned_at',
} %}
with per_entity as (
    {% for name, ts in entities.items() %}
    select
        '{{ name }}' as entity,
        count(*) as raw_rows,
        count(distinct event_id) as distinct_events,
        avg(case when {{ is_late(ts) }} then 1.0 else 0.0 end) as late_arrival_rate,
        avg(case when _tz_adjusted then 1.0 else 0.0 end) as tz_adjusted_rate,
        max({{ ts }}) as latest_event_ts,
        max(date_diff('hour', {{ ts }}, _ingested_at)) as max_arrival_lag_hours
    from {{ source('raw', name) }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
),

q as (
    select entity, count(*) as quarantined_rows
    from {{ source('raw', 'quarantine') }}
    group by 1
)

select
    p.entity,
    p.raw_rows,
    p.distinct_events,
    1 - p.distinct_events::double / nullif(p.raw_rows, 0) as duplicate_rate,
    p.late_arrival_rate,
    p.tz_adjusted_rate,
    coalesce(q.quarantined_rows, 0) as quarantined_rows,
    coalesce(q.quarantined_rows, 0)::double / (p.raw_rows + coalesce(q.quarantined_rows, 0)) as quarantine_rate,
    p.latest_event_ts,
    date_diff('hour', p.latest_event_ts, timestamp '{{ var("data_as_of") }}') as freshness_hours,
    p.max_arrival_lag_hours
from per_entity as p
left join q using (entity)
