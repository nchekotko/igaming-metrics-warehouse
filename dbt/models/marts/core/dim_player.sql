-- Player dimension (SCD1 attributes + lifecycle milestones).
-- Late-arriving dimension handling: player_ids that appear in facts but whose registration record
-- is missing (quarantined / not yet arrived) get an INFERRED member so facts never dangle.
with registered as (
    select * from {{ ref('stg_players') }}
),

seen_in_facts as (
    select player_id from {{ ref('stg_wallet_transactions') }}
    union
    select player_id from {{ ref('stg_bets') }}
    union
    select player_id from {{ ref('stg_game_rounds') }}
    union
    select player_id from {{ ref('stg_bonus_events') }}
    union
    select player_id from {{ ref('stg_rg_events') }}
    union
    select player_id from {{ ref('stg_attribution_events') }}
),

all_players as (
    select player_id, false as is_inferred from registered
    union all
    select s.player_id, true as is_inferred
    from seen_in_facts as s
    where s.player_id not in (select player_id from registered)
),

kyc as (
    select
        player_id,
        min(case when kyc_status = 'verified' then event_ts end) as kyc_verified_at,
        arg_max(kyc_status, event_ts) as kyc_status
    from {{ ref('stg_kyc_events') }}
    group by 1
),

rg as (
    select
        player_id,
        min(case when event_type = 'deposit_limit_set' then event_ts end) as deposit_limit_set_at,
        min(case when event_type = 'self_exclusion' then event_ts end) as self_excluded_at,
        count(case when event_type = 'deposit_limit_hit' then 1 end) as n_deposit_limit_hits
    from {{ ref('stg_rg_events') }}
    group by 1
),

first_attr as (
    select player_id, channel as acquisition_channel, affiliate_id as acquisition_affiliate_id
    from {{ ref('dim_affiliate_attribution') }}
    where version_no = 1
),

current_attr as (
    select player_id, channel as current_channel, affiliate_id as current_affiliate_id,
           version_no as n_attribution_versions
    from {{ ref('dim_affiliate_attribution') }}
    where is_current
)

select
    p.player_id,
    p.is_inferred,
    r.registered_at,
    r.registration_date,
    coalesce(r.country, 'unknown') as country,
    coalesce(r.currency, 'unknown') as currency,
    r.birth_year,
    r.marketing_consent,
    coalesce(fa.acquisition_channel, 'unknown') as acquisition_channel,
    coalesce(fa.acquisition_affiliate_id, 'none') as acquisition_affiliate_id,
    coalesce(ca.current_channel, 'unknown') as current_channel,
    coalesce(ca.current_affiliate_id, 'none') as current_affiliate_id,
    coalesce(ca.n_attribution_versions, 0) as n_attribution_versions,
    coalesce(k.kyc_status, 'not_started') as kyc_status,
    k.kyc_verified_at,
    f.ftd_at,
    f.ftd_month,
    f.ftd_amount_eur,
    rg.deposit_limit_set_at,
    rg.self_excluded_at,
    coalesce(rg.n_deposit_limit_hits, 0) as n_deposit_limit_hits
from all_players as p
left join registered as r using (player_id)
left join kyc as k using (player_id)
left join rg using (player_id)
left join first_attr as fa using (player_id)
left join current_attr as ca using (player_id)
left join {{ ref('int_player_first_deposit') }} as f using (player_id)
