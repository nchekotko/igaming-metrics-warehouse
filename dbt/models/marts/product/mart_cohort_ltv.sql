-- Cumulative NGR per FTD player by months since FTD. All cohorts use ONE definition
-- (var ltv_ngr_version) so early and late cohorts stay comparable across the June policy change.
with cohort as (
    select player_id, ftd_month from {{ ref('dim_player') }} where ftd_month is not null
),

ngr as (
    select
        player_id,
        cast(date_trunc('month', activity_date) as date) as activity_month,
        sum(ngr_eur) as ngr_eur,
        sum(ggr_eur) as ggr_eur
    from {{ ref('fct_ngr_player_daily_versions') }}
    where metric_version = {{ var('ltv_ngr_version') }}
    group by 1, 2
),

sizes as (
    select ftd_month, count(*) as cohort_size from cohort group by 1
),

-- complete months only: sessions that spill past midnight of the last day do not open a new month
months as (
    select distinct activity_month from ngr
    where activity_month < date_trunc('month', timestamp '{{ var("data_as_of") }}')
),

grid as (
    select s.ftd_month, m.activity_month, s.cohort_size
    from sizes as s
    cross join months as m
    where m.activity_month >= s.ftd_month
),

by_month as (
    select
        g.ftd_month,
        date_diff('month', g.ftd_month, g.activity_month) as months_since_ftd,
        g.cohort_size,
        coalesce(sum(n.ngr_eur), 0) as ngr_eur,
        coalesce(sum(n.ggr_eur), 0) as ggr_eur
    from grid as g
    left join cohort as c on c.ftd_month = g.ftd_month
    left join ngr as n on n.player_id = c.player_id and n.activity_month = g.activity_month
    group by 1, 2, 3
)

select
    ftd_month,
    months_since_ftd,
    cohort_size,
    {{ var('ltv_ngr_version') }} as ngr_metric_version,
    ngr_eur,
    sum(ngr_eur) over (partition by ftd_month order by months_since_ftd) / cohort_size
        as cumulative_ngr_per_ftd_eur,
    sum(ggr_eur) over (partition by ftd_month order by months_since_ftd) / cohort_size
        as cumulative_ggr_per_ftd_eur
from by_month
