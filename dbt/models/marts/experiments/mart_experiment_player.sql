-- One row per randomised player: pre-period covariates (for CUPED) and post-period outcomes,
-- plus harm guardrails. Windows: pre = [start - days, start), post = [start, start + days).
{% set start = "date '" ~ var('experiment_start') ~ "'" %}
{% set days = var('experiment_days') %}
with a as (
    select player_id, arm, assigned_at
    from {{ ref('stg_experiment_assignments') }}
    where experiment_id = '{{ var("experiment_id") }}'
),

d as (
    select
        player_id,
        activity_date,
        ngr_eur,
        ggr_eur,
        deposits_eur,
        is_active,
        case
            when activity_date >= {{ start }} - interval '{{ days }} days' and activity_date < {{ start }}
                then 'pre'
            when activity_date >= {{ start }} and activity_date < {{ start }} + interval '{{ days }} days'
                then 'post'
        end as period
    from {{ ref('fct_player_daily') }}
),

agg as (
    select
        player_id,
        sum(case when period = 'pre' then ngr_eur else 0 end) as pre_ngr_eur,
        sum(case when period = 'pre' then deposits_eur else 0 end) as pre_deposits_eur,
        count(case when period = 'pre' and is_active then 1 end) as pre_active_days,
        sum(case when period = 'post' then ngr_eur else 0 end) as post_ngr_eur,
        sum(case when period = 'post' then ggr_eur else 0 end) as post_ggr_eur,
        sum(case when period = 'post' then deposits_eur else 0 end) as post_deposits_eur,
        count(case when period = 'post' and is_active then 1 end) as post_active_days
    from d
    where period is not null
    group by 1
),

rg as (
    select
        player_id,
        bool_or(event_type = 'deposit_limit_hit') as hit_deposit_limit_post,
        bool_or(event_type = 'self_exclusion') as self_excluded_post
    from {{ ref('stg_rg_events') }}
    where event_date >= {{ start }} and event_date < {{ start }} + interval '{{ days }} days'
    group by 1
)

select
    a.player_id,
    a.arm,
    a.assigned_at,
    coalesce(g.pre_ngr_eur, 0) as pre_ngr_eur,
    coalesce(g.pre_deposits_eur, 0) as pre_deposits_eur,
    coalesce(g.pre_active_days, 0) as pre_active_days,
    coalesce(g.post_ngr_eur, 0) as post_ngr_eur,
    coalesce(g.post_ggr_eur, 0) as post_ggr_eur,
    coalesce(g.post_deposits_eur, 0) as post_deposits_eur,
    coalesce(g.post_active_days, 0) as post_active_days,
    coalesce(g.post_active_days, 0) > 0 as is_active_post,
    coalesce(r.hit_deposit_limit_post, false) as hit_deposit_limit_post,
    coalesce(r.self_excluded_post, false) as self_excluded_post
from a
left join agg as g using (player_id)
left join rg as r using (player_id)
