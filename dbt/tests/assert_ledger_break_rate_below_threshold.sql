-- Hard gate: ledger break rate above the fail threshold fails the build.
with r as (
    select avg(case when is_break then 1.0 else 0.0 end) as break_rate from {{ ref('dq_ledger_reconciliation') }}
),
t as (
    select fail_above from {{ ref('dq_thresholds') }} where check_name = 'ledger_break_rate'
)
select r.break_rate, t.fail_above from r cross join t where r.break_rate > t.fail_above
