select affiliate_id, affiliate_name, deal_type, cpa_eur, revshare_pct, qftd_min_deposit_eur
from {{ ref('affiliates') }}
union all
select 'none', 'No affiliate (organic / paid)', 'none', 0, 0, 0
