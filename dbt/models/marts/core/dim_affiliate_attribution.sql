-- SCD2 attribution dimension built on the dbt snapshot. valid_to is exclusive; the current row has
-- valid_to = 9999-12-31. Use `valid_from <= ts and ts < valid_to` for point-in-time joins.
select
    {{ surrogate_key(['player_id', 'dbt_valid_from']) }} as attribution_sk,
    player_id,
    channel,
    affiliate_id,
    reason,
    dbt_valid_from as valid_from,
    dbt_valid_to as valid_to,
    dbt_valid_to = timestamp '9999-12-31 00:00:00' as is_current,
    row_number() over (partition by player_id order by dbt_valid_from) as version_no
from {{ ref('snap_player_attribution') }}
