-- Compliance: withdrawals only after KYC verification. Individual violations are listed as a
-- WARNING (typically the verification event was quarantined or has not arrived yet).
{{ config(severity='warn') }}
select w.tx_id, w.player_id, w.tx_ts, p.kyc_verified_at
from {{ ref('fct_wallet_tx') }} w
join {{ ref('dim_player') }} p using (player_id)
where w.tx_type = 'withdrawal' and (p.kyc_verified_at is null or w.tx_ts < p.kyc_verified_at)
