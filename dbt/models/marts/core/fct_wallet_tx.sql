select
    tx_id,
    player_id,
    tx_ts,
    tx_date,
    tx_type,
    currency,
    amount,
    signed_amount,
    amount_eur,
    payment_method,
    reference_id,
    tz_adjusted,
    is_late,
    _ingested_at
from {{ ref('stg_wallet_transactions') }}
