-- One row per bonus: lifecycle collapsed from grant / wager / convert / forfeit events.
with e as (select * from {{ ref('stg_bonus_events') }})
select
    bonus_id,
    any_value(player_id) as player_id,
    any_value(bonus_type) as bonus_type,
    any_value(campaign_id) as campaign_id,
    min(case when event_type = 'grant' then event_ts end) as granted_at,
    max(case when event_type = 'convert' then event_ts end) as converted_at,
    max(case when event_type = 'forfeit' then event_ts end) as forfeited_at,
    sum(case when event_type = 'grant' then amount_eur else 0 end) as granted_eur,
    sum(case when event_type = 'wager' then amount_eur else 0 end) as wagered_eur,
    max(case when event_type = 'grant' then wagering_requirement end) as wagering_requirement,
    sum(case when event_type = 'convert' then amount_eur else 0 end) as converted_eur,
    sum(case when event_type = 'forfeit' then amount_eur else 0 end) as forfeited_eur,
    case
        when count(case when event_type = 'convert' then 1 end) > 0 then 'converted'
        when count(case when event_type = 'forfeit' then 1 end) > 0 then 'forfeited'
        when count(case when event_type = 'grant' then 1 end) = 0 then 'orphan_no_grant'
        else 'active'
    end as bonus_state
from e
group by bonus_id
