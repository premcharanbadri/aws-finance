select
    customer_id,
    signup_date::date                         as signup_date,
    nullif(acquisition_campaign_id, '')       as acquisition_campaign_id,
    acquisition_channel,
    state,
    age_band,
    product,
    first_funding_amount_usd::decimal(14, 2)  as first_funding_amount_usd,
    is_active_90d = '1'                       as is_active_90d
from {{ source('raw_marketing', 'customers') }}
