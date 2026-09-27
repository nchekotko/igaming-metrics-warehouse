-- Reported NGR (the version valid at the time) vs NGR restated under every registered version.
-- Reproducibility artefact: the same seed + registry always yields the same numbers, and adding a
-- version never rewrites what was reported for earlier periods.
with m as (
    select
        cast(date_trunc('month', activity_date) as date) as activity_month,
        metric_version,
        sum(ggr_eur) as ggr_eur,
        sum(ngr_eur) as ngr_eur,
        sum(case when is_applicable then ngr_eur else 0 end) as reported_part_eur,
        bool_or(is_applicable) as is_applicable_version
    from {{ ref('fct_ngr_player_daily_versions') }}
    group by 1, 2
),

reported as (
    select activity_month, sum(reported_part_eur) as ngr_reported_eur
    from m
    group by 1
)

select
    m.activity_month,
    m.metric_version,
    m.is_applicable_version,
    m.ggr_eur,
    m.ngr_eur as ngr_restated_eur,
    r.ngr_reported_eur,
    m.ngr_eur - r.ngr_reported_eur as delta_vs_reported_eur,
    case
        when r.ngr_reported_eur <> 0 then (m.ngr_eur - r.ngr_reported_eur) / abs(r.ngr_reported_eur)
    end as delta_vs_reported_pct
from m
inner join reported as r using (activity_month)
