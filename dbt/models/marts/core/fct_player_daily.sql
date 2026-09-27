-- Player x UTC-day fact with the REPORTED NGR (the metric version valid on that day) and the
-- point-in-time attribution (SCD2) of the player at the end of that day.
with c as (
    select * from {{ ref('int_player_day_components') }}
),

ngr as (
    select player_id, activity_date, metric_version, bonus_cost_eur, gaming_tax_eur, ngr_eur
    from {{ ref('fct_ngr_player_daily_versions') }}
    where is_applicable
),

attr as (
    select player_id, channel, affiliate_id, valid_from, valid_to
    from {{ ref('dim_affiliate_attribution') }}
)

select
    {{ surrogate_key(['c.player_id', 'c.activity_date']) }} as player_day_sk,
    c.player_id,
    c.activity_date,
    cast(date_trunc('month', c.activity_date) as date) as activity_month,
    coalesce(a.channel, 'unknown') as channel_pit,
    coalesce(a.affiliate_id, 'none') as affiliate_id_pit,
    c.is_active,
    c.n_bets,
    c.n_rounds,
    c.n_deposits,
    c.deposits_eur,
    c.withdrawals_eur,
    c.chargebacks_eur,
    c.sb_stake_eur,
    c.casino_stake_eur,
    c.ggr_sportsbook_eur,
    c.ggr_casino_eur,
    c.ggr_eur,
    c.bonus_granted_eur,
    c.bonus_converted_eur,
    c.payment_fees_eur,
    c.provider_fees_eur,
    n.gaming_tax_eur,
    n.bonus_cost_eur,
    n.metric_version as ngr_metric_version,
    n.ngr_eur
from c
inner join ngr as n using (player_id, activity_date)
left join attr as a
    on
        a.player_id = c.player_id
        and a.valid_from < c.activity_date + interval 1 day
        and a.valid_to >= c.activity_date + interval 1 day
