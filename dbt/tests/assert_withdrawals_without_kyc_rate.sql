-- Hard gate: more than 1% of withdrawals without a prior KYC verification fails the build.
with w as (
    select avg(case when p.kyc_verified_at is null or w.tx_ts < p.kyc_verified_at then 1.0 else 0.0 end) as rate
    from {{ ref('fct_wallet_tx') }} w
    join {{ ref('dim_player') }} p using (player_id)
    where w.tx_type = 'withdrawal'
)
select rate from w where rate > 0.01
