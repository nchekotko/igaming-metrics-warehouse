-- Each player-day has exactly one applicable NGR version.
select player_id, activity_date, count(*) as n
from {{ ref('fct_ngr_player_daily_versions') }}
where is_applicable
group by 1, 2
having count(*) <> 1
