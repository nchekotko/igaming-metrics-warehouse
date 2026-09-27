with src as ({{ dedup_first_arrival(source('raw', 'players')) }})
select
    player_id,
    registered_at,
    cast(registered_at as date) as registration_date,
    country,
    currency,
    birth_year,
    marketing_consent,
    _ingested_at,
    {{ is_late('registered_at') }} as is_late
from src
