{#-
  At-least-once delivery => the same event_id can land twice. Keep the FIRST arrival so that
  incremental models keyed on _ingested_at never see a "new" copy of an old event.
-#}
{% macro dedup_first_arrival(relation, key='event_id') -%}
    select *
    from {{ relation }}
    qualify row_number() over (partition by {{ key }} order by _ingested_at, _loaded_at, _batch_id) = 1
{%- endmacro %}

{% macro is_late(event_ts, ingested_ts='_ingested_at') -%}
    ({{ ingested_ts }} > {{ event_ts }} + interval '{{ var("late_threshold_hours") }} hours')
{%- endmacro %}
