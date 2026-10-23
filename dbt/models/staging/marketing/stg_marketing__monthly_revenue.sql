select
    "month"::date                            as revenue_month,
    product,
    channel,
    new_customers::int                       as new_customers,
    interchange_revenue_usd::decimal(14, 2)  as interchange_revenue_usd,
    interest_revenue_usd::decimal(14, 2)     as interest_revenue_usd,
    fee_revenue_usd::decimal(14, 2)          as fee_revenue_usd
from {{ source('raw_marketing', 'monthly_revenue') }}
