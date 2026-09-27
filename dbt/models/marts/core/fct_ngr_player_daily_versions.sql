-- NGR for EVERY registered definition version on EVERY player-day.
-- is_applicable marks the version valid on that date (what finance reports); the other rows are
-- "restated" numbers. Because components and definitions are both stored, any historical figure
-- can be reproduced exactly, and a definition change can be quantified before it goes live.
with c as (
    select * from {{ ref('int_player_day_components') }}
),

v as (
    select * from {{ ref('int_metric_versions') }} where metric_name = 'ngr'
),

tax as (
    select
        c.player_id,
        c.activity_date,
        c.ggr_sportsbook_eur * coalesce(ts.rate, 0) + c.ggr_casino_eur * coalesce(tc.rate, 0) as gaming_tax_eur
    from c
    left join {{ ref('gaming_tax_rates') }} as ts
        on
            ts.product = 'sportsbook'
            and c.activity_date >= ts.valid_from
            and c.activity_date <= coalesce(ts.valid_to, date '9999-12-31')
    left join {{ ref('gaming_tax_rates') }} as tc
        on
            tc.product = 'casino'
            and c.activity_date >= tc.valid_from
            and c.activity_date <= coalesce(tc.valid_to, date '9999-12-31')
),

applied as (
    select
        c.player_id,
        c.activity_date,
        v.version as metric_version,
        c.activity_date >= v.valid_from and c.activity_date < v.valid_to_excl as is_applicable,
        c.ggr_eur,
        case
            when not v.deduct_bonus_cost then 0
            when v.bonus_cost_basis = 'converted' then c.bonus_converted_eur
            when v.bonus_cost_basis = 'granted' then c.bonus_granted_eur
            else 0
        end as bonus_cost_eur,
        case when v.deduct_payment_fees then c.payment_fees_eur else 0 end as payment_fees_eur,
        case when v.deduct_gaming_tax then t.gaming_tax_eur else 0 end as gaming_tax_eur,
        case when v.deduct_provider_fees then c.provider_fees_eur else 0 end as provider_fees_eur,
        case when v.deduct_chargebacks then c.chargebacks_eur else 0 end as chargebacks_eur
    from c
    cross join v
    inner join tax as t using (player_id, activity_date)
)

select
    {{ surrogate_key(['player_id', 'activity_date', 'metric_version']) }} as player_day_version_sk,
    *,
    ggr_eur - bonus_cost_eur - payment_fees_eur - gaming_tax_eur - provider_fees_eur - chargebacks_eur
        as ngr_eur
from applied
