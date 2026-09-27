-- FTD-month cohorts: share of the cohort active (placed a bet or played a round) in month N.
with cohort as (
    select player_id, ftd_month from {{ ref('dim_player') }} where ftd_month is not null
),

activity as (
    select distinct player_id, activity_month
    from {{ ref('fct_player_daily') }}
    where is_active
),

sizes as (
    select ftd_month, count(*) as cohort_size from cohort group by 1
),

-- complete months only: sessions that spill past midnight of the last day do not open a new month
months as (
    select distinct activity_month from {{ ref('fct_player_daily') }}
    where activity_month < date_trunc('month', timestamp '{{ var("data_as_of") }}')
),

grid as (
    select s.ftd_month, m.activity_month
    from sizes as s
    cross join months as m
    where m.activity_month >= s.ftd_month
)

select
    g.ftd_month,
    date_diff('month', g.ftd_month, g.activity_month) as months_since_ftd,
    s.cohort_size,
    count(a.player_id) as active_players,
    count(a.player_id)::double / s.cohort_size as retention_rate
from grid as g
inner join sizes as s using (ftd_month)
left join cohort as c on c.ftd_month = g.ftd_month
left join activity as a on a.player_id = c.player_id and a.activity_month = g.activity_month
group by 1, 2, 3
