-- Player x experiment-day grid with cumulative outcomes: input for sequential monitoring (mSPRT).
{% set start = "date '" ~ var('experiment_start') ~ "'" %}
with a as (
    select player_id, arm
    from {{ ref('stg_experiment_assignments') }}
    where experiment_id = '{{ var("experiment_id") }}'
),

days as (
    select range + 1 as day_index, {{ start }} + cast(range as integer) as activity_date
    from range({{ var('experiment_days') }})
),

daily as (
    select player_id, activity_date, ngr_eur, deposits_eur
    from {{ ref('fct_player_daily') }}
    where activity_date >= {{ start }} and activity_date < {{ start }} + interval '{{ var("experiment_days") }} days'
),

grid as (
    select
        a.player_id,
        a.arm,
        d.day_index,
        d.activity_date,
        coalesce(x.ngr_eur, 0) as ngr_eur,
        coalesce(x.deposits_eur, 0) as deposits_eur
    from a
    cross join days as d
    left join daily as x on x.player_id = a.player_id and x.activity_date = d.activity_date
)

select
    player_id,
    arm,
    day_index,
    activity_date,
    sum(ngr_eur) over (partition by player_id order by day_index) as cum_ngr_eur,
    sum(deposits_eur) over (partition by player_id order by day_index) as cum_deposits_eur
from grid
