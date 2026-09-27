-- Reported NGR in the restatement mart equals the NGR in fct_player_daily, month by month.
with r as (select distinct activity_month, ngr_reported_eur from {{ ref('mart_metric_restatement') }}),
d as (select activity_month, sum(ngr_eur) as v from {{ ref('fct_player_daily') }} group by 1)
select * from r join d using (activity_month) where abs(r.ngr_reported_eur - d.v) > 0.01
