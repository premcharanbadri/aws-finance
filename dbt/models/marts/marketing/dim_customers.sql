-- One row per funded customer with acquisition attribution.
select
    cu.customer_id,
    cu.signup_date,
    date_trunc('month', cu.signup_date)::date  as signup_month,
    cu.acquisition_campaign_id,
    c.campaign_name                            as acquisition_campaign_name,
    cu.acquisition_channel,
    cu.acquisition_campaign_id is null         as is_organic,
    cu.state,
    cu.age_band,
    cu.product,
    cu.first_funding_amount_usd,
    cu.is_active_90d
from {{ ref('stg_marketing__customers') }} cu
left join {{ ref('stg_marketing__campaigns') }} c
    on c.campaign_id = cu.acquisition_campaign_id
