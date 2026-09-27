-- GGR must agree across grains: player-day fact vs bet fact + round fact.
with a as (select sum(ggr_eur) as v from {{ ref('fct_player_daily') }}),
b as (
    select (select sum(ggr_eur) from {{ ref('fct_bets') }} where settled_date is not null)
         + (select sum(ggr_eur) from {{ ref('fct_game_rounds') }}) as v
)
select a.v as player_daily, b.v as facts from a cross join b where abs(a.v - b.v) > 0.01
