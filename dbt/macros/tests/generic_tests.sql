{# ---------------------------------------------------------------- custom generic tests #}

{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}


{% test expression_is_true(model, expression, column_name=none) %}
select * from {{ model }} where not ({{ expression }})
{% endtest %}


{#- Share of raw rows that are repeated deliveries of an event_id already seen. -#}
{% test duplicate_rate_below(model, key, max_rate) %}
with s as (
    select count(*) as n_rows, count(distinct {{ key }}) as n_distinct from {{ model }}
)
select n_rows, n_distinct, 1 - n_distinct::double / nullif(n_rows, 0) as duplicate_rate
from s
where 1 - n_distinct::double / nullif(n_rows, 0) > {{ max_rate }}
{% endtest %}


{#- Share of events ingested more than late_threshold_hours after they happened. -#}
{% test late_arrival_rate_below(model, event_ts, max_rate) %}
with s as (
    select avg(case when {{ is_late(event_ts) }} then 1.0 else 0.0 end) as late_rate from {{ model }}
)
select late_rate from s where late_rate > {{ max_rate }}
{% endtest %}


{#- Event-time freshness relative to var('data_as_of'), not wall clock: the demo data is historical. -#}
{% test event_freshness_within(model, column_name, max_hours) %}
with s as (select max({{ column_name }}) as latest from {{ model }})
select latest,
       date_diff('hour', latest, timestamp '{{ var("data_as_of") }}') as hours_behind
from s
where latest is null
   or date_diff('hour', latest, timestamp '{{ var("data_as_of") }}') > {{ max_hours }}
{% endtest %}


{#- SCD2 / validity windows must not overlap within a key. valid_to is exclusive. -#}
{% test no_overlapping_intervals(model, partition_by, valid_from, valid_to) %}
select a.{{ partition_by }}, a.{{ valid_from }} as a_from, a.{{ valid_to }} as a_to,
       b.{{ valid_from }} as b_from, b.{{ valid_to }} as b_to
from {{ model }} a
join {{ model }} b
  on a.{{ partition_by }} = b.{{ partition_by }}
 and a.{{ valid_from }} < b.{{ valid_from }}
 and b.{{ valid_from }} < a.{{ valid_to }}
{% endtest %}
