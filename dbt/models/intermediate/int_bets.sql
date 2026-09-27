-- One row per bet = latest known state. A settlement record wins over the open record even if it
-- arrived earlier (out-of-order delivery): ordering is by state and event_ts, never by arrival.
with states as (
    select
        *,
        row_number() over (
            partition by bet_id
            order by case when status = 'open' then 0 else 1 end desc, event_ts desc, bet_event_id desc
        ) as rn,
        count(*) over (partition by bet_id) as n_state_events
    from {{ ref('stg_bets') }}
),

legs as (
    select bet_id, count(*) as n_legs_reported
    from (
        select bet_id, leg_no
        from {{ ref('stg_bet_legs') }}
        qualify row_number() over (partition by bet_id, leg_no order by event_ts desc) = 1
    )
    group by 1
)

select
    s.bet_id,
    s.player_id,
    s.placed_at,
    cast(s.placed_at as date) as placed_date,
    s.settled_at,
    cast(s.settled_at as date) as settled_date,
    s.status,
    s.status in ('won', 'lost') as is_ggr_relevant,
    s.bet_type,
    s.stake,
    s.total_odds,
    s.payout,
    s.currency,
    round(s.stake * fx_p.eur_per_unit, 4) as stake_eur,
    round(s.payout * coalesce(fx_s.eur_per_unit, fx_p.eur_per_unit), 4) as payout_eur,
    -- GGR is recognised on settlement and converted at the settlement month's rate
    case
        when s.status in ('won', 'lost') then round((s.stake - s.payout) * fx_s.eur_per_unit, 4)
        else 0
    end as ggr_eur,
    s.n_state_events,
    coalesce(l.n_legs_reported, 0) as n_legs_reported
from states as s
left join legs as l using (bet_id)
left join {{ ref('fx_rates') }} as fx_p
    on fx_p.currency = s.currency and fx_p.month = date_trunc('month', s.placed_at)
left join {{ ref('fx_rates') }} as fx_s
    on fx_s.currency = s.currency and fx_s.month = date_trunc('month', s.settled_at)
where s.rn = 1
