-- Compliance: no wagering after self-exclusion.
with ex as (select player_id, self_excluded_at from {{ ref('dim_player') }} where self_excluded_at is not null),
w as (
    select player_id, placed_at as ts, 'bet' as kind from {{ ref('fct_bets') }}
    union all
    select player_id, round_ts, 'round' from {{ ref('fct_game_rounds') }}
)
select w.* , ex.self_excluded_at from w join ex using (player_id) where w.ts > ex.self_excluded_at
