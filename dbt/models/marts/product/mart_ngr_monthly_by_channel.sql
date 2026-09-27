-- Monthly GGR / NGR by channel. Channel = point-in-time attribution (SCD2) on the activity day, so
-- a player re-attributed in June moves to the new channel from June on, not retroactively.
select
    activity_month,
    channel_pit as channel,
    count(distinct case when is_active then player_id end) as active_players,
    sum(deposits_eur) as deposits_eur,
    sum(ggr_eur) as ggr_eur,
    sum(bonus_cost_eur) as bonus_cost_eur,
    sum(ngr_eur) as ngr_eur,
    max(ngr_metric_version) as ngr_metric_version
from {{ ref('fct_player_daily') }}
group by 1, 2
