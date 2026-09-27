-- Wallet ledger in native currency + EUR. signed_amount is the effect on the player's cash balance.
with src as ({{ dedup_first_arrival(source('raw', 'wallet_transactions')) }})
select
    s.event_id as tx_id,
    s.player_id,
    s.tx_ts,
    cast(s.tx_ts as date) as tx_date,
    s.tx_type,
    cast(s.amount as decimal(18, 2)) as amount,
    case when s.tx_type in ('deposit', 'bet_payout', 'bet_refund', 'casino_win', 'bonus_convert')
         then s.amount else -s.amount end as signed_amount,
    s.currency,
    round(s.amount * fx.eur_per_unit, 4) as amount_eur,
    s.payment_method,
    s.reference_id,
    s._ingested_at,
    s._tz_adjusted as tz_adjusted,
    {{ is_late('s.tx_ts', 's._ingested_at') }} as is_late
from src s
left join {{ ref('fx_rates') }} fx
  on fx.currency = s.currency and fx.month = date_trunc('month', s.tx_ts)
