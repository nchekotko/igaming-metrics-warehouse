-- Metric registry with an exclusive valid_to (open-ended versions -> 9999-12-31).
select
    metric_name,
    version,
    valid_from,
    coalesce(cast(valid_to + interval 1 day as date), date '9999-12-31') as valid_to_excl,
    bonus_cost_basis,
    deduct_bonus_cost,
    deduct_payment_fees,
    deduct_gaming_tax,
    deduct_provider_fees,
    deduct_chargebacks,
    definition,
    change_note
from {{ ref('metric_definitions') }}
