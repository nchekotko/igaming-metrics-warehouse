-- Metric versions of the same metric must not overlap and must not leave gaps.
with v as (
    select metric_name, version, valid_from, valid_to_excl,
           lead(valid_from) over (partition by metric_name order by valid_from) as next_from
    from {{ ref('int_metric_versions') }}
)
select * from v where next_from is not null and next_from <> valid_to_excl
