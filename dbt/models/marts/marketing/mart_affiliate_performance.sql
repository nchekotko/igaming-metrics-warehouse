-- Affiliate x FTD-month performance.
-- Deal attribution = the SCD2 row valid AT THE FTD MOMENT (what the affiliate is paid on).
-- ftd_reattributed_later counts FTDs whose current attribution differs (disputes, fraud reversals).
-- QFTD = deposits within qftd_window_days >= affiliate threshold AND at least one bet/round.
with ftd as (
    select * from {{ ref('int_player_first_deposit') }}
),

pit as (
    select
        f.player_id,
        f.ftd_month,
        f.ftd_at,
        f.deposits_in_qftd_window_eur,
        f.bets_and_rounds_in_window,
        coalesce(a.affiliate_id, 'none') as affiliate_id
    from ftd as f
    left join {{ ref('dim_affiliate_attribution') }} as a
        on a.player_id = f.player_id and a.valid_from <= f.ftd_at and f.ftd_at < a.valid_to
),

cur as (
    select player_id, affiliate_id as current_affiliate_id
    from {{ ref('dim_affiliate_attribution') }}
    where is_current
),

players as (
    select
        p.*,
        d.deal_type,
        d.cpa_eur,
        d.revshare_pct,
        (p.deposits_in_qftd_window_eur >= d.qftd_min_deposit_eur and p.bets_and_rounds_in_window >= 1)
            as is_qftd,
        coalesce(c.current_affiliate_id, 'none') <> p.affiliate_id as is_reattributed_later
    from pit as p
    inner join {{ ref('dim_affiliate') }} as d using (affiliate_id)
    left join cur as c using (player_id)
    where p.affiliate_id <> 'none'
),

player_value as (
    select
        d.player_id,
        sum(d.deposits_eur) as deposits_eur,
        sum(d.ggr_eur) as ggr_eur,
        sum(d.bonus_cost_eur) as bonus_cost_eur,
        sum(d.ngr_eur) as ngr_eur
    from {{ ref('fct_player_daily') }} as d
    where d.player_id in (select player_id from players)
    group by 1
),

agg as (
    select
        p.affiliate_id,
        p.ftd_month,
        any_value(p.deal_type) as deal_type,
        count(*) as ftd,
        count(case when p.is_qftd then 1 end) as qftd,
        count(case when p.is_reattributed_later then 1 end) as ftd_reattributed_later,
        sum(coalesce(v.deposits_eur, 0)) as deposits_eur,
        sum(coalesce(v.ggr_eur, 0)) as ggr_eur,
        sum(coalesce(v.bonus_cost_eur, 0)) as bonus_cost_eur,
        sum(coalesce(v.ngr_eur, 0)) as ngr_eur,
        sum(case when p.is_qftd then p.cpa_eur else 0 end) as cpa_cost_eur,
        any_value(p.revshare_pct) as revshare_pct
    from players as p
    left join player_value as v using (player_id)
    group by 1, 2
)

select
    affiliate_id,
    ftd_month,
    deal_type,
    ftd,
    qftd,
    ftd_reattributed_later,
    deposits_eur,
    ggr_eur,
    bonus_cost_eur,
    ngr_eur,
    cpa_cost_eur,
    revshare_pct,
    -- simplification: rev-share on positive NGR, no negative carry-over between months
    greatest(ngr_eur, 0) * revshare_pct as revshare_cost_eur,
    cpa_cost_eur + greatest(ngr_eur, 0) * revshare_pct as affiliate_cost_eur,
    ngr_eur - cpa_cost_eur - greatest(ngr_eur, 0) * revshare_pct as contribution_eur,
    qftd::double / nullif(ftd, 0) as qftd_rate,
    ngr_eur / nullif(ftd, 0) as ngr_per_ftd_eur
from agg
