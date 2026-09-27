-- Soft signal: individual breaks are expected (injected lost callbacks / double credits /
-- quarantined records) and are surfaced as a WARNING with the offending rows.
{{ config(severity='warn') }}
select * from {{ ref('dq_ledger_reconciliation') }} where is_break
