-- Latest known result per (bet, leg).
select
    {{ surrogate_key(['bet_id', 'leg_no']) }} as bet_leg_sk,
    bet_id,
    leg_no,
    sport,
    market,
    odds,
    leg_result,
    event_ts as last_update_at
from {{ ref('stg_bet_legs') }}
qualify row_number() over (partition by bet_id, leg_no order by event_ts desc, leg_event_id desc) = 1
