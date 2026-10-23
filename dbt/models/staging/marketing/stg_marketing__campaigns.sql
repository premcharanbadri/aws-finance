select
    campaign_id,
    name                         as campaign_name,
    channel,
    objective,
    product,
    start_date::date             as start_date,
    end_date::date               as end_date,
    budget_usd::decimal(14, 2)   as budget_usd
from {{ source('raw_marketing', 'campaigns') }}
