-- Ledger reconciliation: for every player x UTC day x component, the wallet must equal what the
-- product systems say happened. Native currency (no FX noise). A break means money moved without
-- a product event or vice versa: lost win callbacks, double credits, quarantined records, etc.
with expected as (
    select player_id, placed_date as d, 'bet_stake' as component, sum(stake) as amount
    from {{ ref('int_bets') }} group by 1, 2
    union all
    select player_id, settled_date, 'bet_payout', sum(payout)
    from {{ ref('int_bets') }} where status = 'won' group by 1, 2
    union all
    select player_id, settled_date, 'bet_refund', sum(payout)
    from {{ ref('int_bets') }} where status = 'void' group by 1, 2
    union all
    select player_id, round_date, 'casino_stake', sum(stake)
    from {{ ref('stg_game_rounds') }} group by 1, 2
    union all
    select player_id, round_date, 'casino_win', sum(win)
    from {{ ref('stg_game_rounds') }} where win > 0 group by 1, 2
    union all
    select player_id, event_date, 'bonus_convert', sum(amount)
    from {{ ref('stg_bonus_events') }} where event_type = 'convert' group by 1, 2
),

actual as (
    select player_id, tx_date as d, tx_type as component, sum(amount) as amount, count(*) as n_tx
    from {{ ref('stg_wallet_transactions') }}
    where tx_type in ('bet_stake', 'bet_payout', 'bet_refund', 'casino_stake', 'casino_win', 'bonus_convert')
    group by 1, 2, 3
),

-- player-days that have at least one quarantined wallet / product record: a break there is
-- "explained" (the record exists but violated its contract) rather than money going missing.
quarantined as (
    select distinct
        json_extract_string(raw_line, '$.payload.player_id') as player_id,
        try_cast(left(coalesce(
            json_extract_string(raw_line, '$.payload.tx_ts'),
            json_extract_string(raw_line, '$.payload.round_ts'),
            json_extract_string(raw_line, '$.payload.settled_at'),
            json_extract_string(raw_line, '$.payload.placed_at'),
            json_extract_string(raw_line, '$.payload.event_ts')
        ), 10) as date) as d
    from {{ source('raw', 'quarantine') }}
    where entity in ('wallet_transactions', 'bets', 'game_rounds', 'bonus_events')
      and json_valid(raw_line)
),

joined as (
    select
        coalesce(e.player_id, a.player_id) as player_id,
        coalesce(e.d, a.d) as activity_date,
        coalesce(e.component, a.component) as component,
        e.amount as e_amount,
        a.amount as a_amount
    from expected as e
    full outer join actual as a
        on a.player_id = e.player_id and a.d = e.d and a.component = e.component
)

select
    {{ surrogate_key(['j.player_id', 'j.activity_date', 'j.component']) }} as recon_sk,
    j.player_id,
    j.activity_date,
    j.component,
    coalesce(j.e_amount, 0) as expected_amount,
    coalesce(j.a_amount, 0) as wallet_amount,
    coalesce(j.a_amount, 0) - coalesce(j.e_amount, 0) as diff,
    abs(coalesce(j.a_amount, 0) - coalesce(j.e_amount, 0)) > {{ var('recon_tolerance') }} as is_break,
    exists (
        select 1 from quarantined as q
        where q.player_id = j.player_id and q.d between j.activity_date - 1 and j.activity_date + 1
    ) as has_quarantined_record,
    case
        when j.e_amount is null then 'wallet_without_product_event'
        when j.a_amount is null then 'product_event_without_wallet'
        when j.a_amount > j.e_amount then 'wallet_over'
        when j.a_amount < j.e_amount then 'wallet_under'
        else 'ok'
    end as break_type
from joined as j
