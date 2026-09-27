-- One row per (check, entity) with value, thresholds and a pass / warn / fail status.
-- Thresholds live in the dq_thresholds seed (entity-specific row overrides the '*' default).
with health as (
    select * from {{ ref('dq_entity_health') }}
),

checks as (
    select 'duplicate_rate' as check_name, entity, duplicate_rate as value from health
    union all
    select 'late_arrival_rate', entity, late_arrival_rate from health
    union all
    select 'quarantine_rate', entity, quarantine_rate from health
    union all
    -- sparse streams (assignments, rg, kyc, attribution) are not expected to be fresh daily
    select 'freshness_hours', entity, freshness_hours::double
    from health
    where entity in ('players', 'wallet_transactions', 'bets', 'game_rounds', 'bonus_events')
    union all
    select 'ledger_break_rate', 'wallet_transactions', avg(case when is_break then 1.0 else 0.0 end)
    from {{ ref('dq_ledger_reconciliation') }}
    union all
    -- breaks NOT explained by a quarantined record: money moved (or did not) for no visible reason
    select 'ledger_unexplained_break_rate', 'wallet_transactions',
           avg(case when is_break and not has_quarantined_record then 1.0 else 0.0 end)
    from {{ ref('dq_ledger_reconciliation') }}
),

thresholds as (
    select c.check_name, c.entity, c.value,
           coalesce(s.warn_above, d.warn_above) as warn_above,
           coalesce(s.fail_above, d.fail_above) as fail_above,
           coalesce(s.description, d.description) as description
    from checks as c
    left join {{ ref('dq_thresholds') }} as s on s.check_name = c.check_name and s.entity = c.entity
    left join {{ ref('dq_thresholds') }} as d on d.check_name = c.check_name and d.entity = '*'
)

select
    check_name,
    entity,
    round(value, 6) as value,
    warn_above,
    fail_above,
    case
        when value > fail_above then 'fail'
        when value > warn_above then 'warn'
        else 'pass'
    end as status,
    description
from thresholds
