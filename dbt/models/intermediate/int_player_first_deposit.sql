-- FTD per player plus what happened inside the QFTD window (deposits, bets/rounds).
with deposits as (
    select
        player_id,
        tx_ts,
        amount_eur,
        row_number() over (partition by player_id order by tx_ts, tx_id) as n
    from {{ ref('stg_wallet_transactions') }}
    where tx_type = 'deposit'
),

ftd as (
    select player_id, tx_ts as ftd_at, amount_eur as ftd_amount_eur
    from deposits
    where n = 1
),

window_deposits as (
    select f.player_id, sum(d.amount_eur) as deposits_in_qftd_window_eur
    from ftd as f
    inner join deposits as d
        on
            d.player_id = f.player_id
            and d.tx_ts < f.ftd_at + interval '{{ var("qftd_window_days") }} days'
    group by 1
),

activity as (
    select player_id, placed_at as ts from {{ ref('int_bets') }}
    union all
    select player_id, round_ts as ts from {{ ref('stg_game_rounds') }}
),

window_activity as (
    select f.player_id, count(*) as bets_and_rounds_in_window
    from ftd as f
    inner join activity as a
        on
            a.player_id = f.player_id
            and a.ts >= f.ftd_at
            and a.ts < f.ftd_at + interval '{{ var("qftd_window_days") }} days'
    group by 1
)

select
    f.player_id,
    f.ftd_at,
    cast(f.ftd_at as date) as ftd_date,
    cast(date_trunc('month', f.ftd_at) as date) as ftd_month,
    f.ftd_amount_eur,
    coalesce(w.deposits_in_qftd_window_eur, 0) as deposits_in_qftd_window_eur,
    coalesce(a.bets_and_rounds_in_window, 0) as bets_and_rounds_in_window
from ftd as f
left join window_deposits as w using (player_id)
left join window_activity as a using (player_id)
