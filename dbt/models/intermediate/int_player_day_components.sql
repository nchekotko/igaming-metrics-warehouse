-- Player x UTC-day grain: every building block needed by GGR/NGR (EUR) BEFORE any definition is
-- applied. Definitions (metric versions) are applied in fct_ngr_player_daily_versions.
with sb_settled as (
    select
        player_id,
        settled_date as activity_date,
        sum(case when is_ggr_relevant then stake_eur else 0 end) as sb_settled_stake_eur,
        sum(case when is_ggr_relevant then payout_eur else 0 end) as sb_payout_eur,
        sum(ggr_eur) as ggr_sportsbook_eur
    from {{ ref('int_bets') }}
    where settled_date is not null
    group by 1, 2
),

sb_placed as (
    select player_id, placed_date as activity_date, count(*) as n_bets, sum(stake_eur) as sb_stake_eur
    from {{ ref('int_bets') }}
    group by 1, 2
),

casino_provider as (
    select
        player_id,
        round_date as activity_date,
        provider_id,
        sum(stake_eur) as stake_eur,
        sum(win_eur) as win_eur,
        count(*) as n_rounds
    from {{ ref('stg_game_rounds') }}
    group by 1, 2, 3
),

casino as (
    select
        c.player_id,
        c.activity_date,
        sum(c.n_rounds) as n_rounds,
        sum(c.stake_eur) as casino_stake_eur,
        sum(c.win_eur) as casino_win_eur,
        sum(c.stake_eur - c.win_eur) as ggr_casino_eur,
        -- simplification: provider rev-share applied to player-day GGR (can be negative)
        sum((c.stake_eur - c.win_eur) * p.rev_share_pct) as provider_fees_eur
    from casino_provider as c
    inner join {{ ref('provider_fee_schedule') }} as p using (provider_id)
    group by 1, 2
),

wallet as (
    select
        w.player_id,
        w.tx_date as activity_date,
        sum(case when w.tx_type = 'deposit' then w.amount_eur else 0 end) as deposits_eur,
        count(case when w.tx_type = 'deposit' then 1 end) as n_deposits,
        sum(case when w.tx_type = 'withdrawal' then w.amount_eur else 0 end) as withdrawals_eur,
        sum(case when w.tx_type = 'chargeback' then w.amount_eur else 0 end) as chargebacks_eur,
        sum(
            case
                when w.tx_type in ('deposit', 'withdrawal', 'chargeback')
                    then w.amount_eur * f.pct_fee + f.fixed_fee_eur
                else 0
            end
        ) as payment_fees_eur
    from {{ ref('stg_wallet_transactions') }} as w
    left join {{ ref('payment_fee_schedule') }} as f
        on f.payment_method = w.payment_method and f.tx_type = w.tx_type
    group by 1, 2
),

bonus as (
    select
        player_id,
        event_date as activity_date,
        sum(case when event_type = 'grant' then amount_eur else 0 end) as bonus_granted_eur,
        sum(case when event_type = 'convert' then amount_eur else 0 end) as bonus_converted_eur
    from {{ ref('stg_bonus_events') }}
    group by 1, 2
),

keys as (
    select player_id, activity_date from sb_settled
    union
    select player_id, activity_date from sb_placed
    union
    select player_id, activity_date from casino
    union
    select player_id, activity_date from wallet
    union
    select player_id, activity_date from bonus
)

select
    k.player_id,
    k.activity_date,
    coalesce(sp.n_bets, 0) as n_bets,
    coalesce(sp.sb_stake_eur, 0) as sb_stake_eur,
    coalesce(ss.sb_settled_stake_eur, 0) as sb_settled_stake_eur,
    coalesce(ss.sb_payout_eur, 0) as sb_payout_eur,
    coalesce(ss.ggr_sportsbook_eur, 0) as ggr_sportsbook_eur,
    coalesce(c.n_rounds, 0) as n_rounds,
    coalesce(c.casino_stake_eur, 0) as casino_stake_eur,
    coalesce(c.casino_win_eur, 0) as casino_win_eur,
    coalesce(c.ggr_casino_eur, 0) as ggr_casino_eur,
    coalesce(c.provider_fees_eur, 0) as provider_fees_eur,
    coalesce(w.deposits_eur, 0) as deposits_eur,
    coalesce(w.n_deposits, 0) as n_deposits,
    coalesce(w.withdrawals_eur, 0) as withdrawals_eur,
    coalesce(w.chargebacks_eur, 0) as chargebacks_eur,
    coalesce(w.payment_fees_eur, 0) as payment_fees_eur,
    coalesce(b.bonus_granted_eur, 0) as bonus_granted_eur,
    coalesce(b.bonus_converted_eur, 0) as bonus_converted_eur,
    coalesce(ss.ggr_sportsbook_eur, 0) + coalesce(c.ggr_casino_eur, 0) as ggr_eur,
    (coalesce(sp.n_bets, 0) + coalesce(c.n_rounds, 0)) > 0 as is_active
from keys as k
left join sb_settled as ss using (player_id, activity_date)
left join sb_placed as sp using (player_id, activity_date)
left join casino as c using (player_id, activity_date)
left join wallet as w using (player_id, activity_date)
left join bonus as b using (player_id, activity_date)
