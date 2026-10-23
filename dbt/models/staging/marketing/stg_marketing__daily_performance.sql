select
    "date"::date                 as activity_date,
    campaign_id,
    impressions::bigint          as impressions,
    clicks::bigint               as clicks,
    spend_usd::decimal(14, 2)    as spend_usd,
    signups::int                 as signups,
    funded_accounts::int         as funded_accounts
from {{ source('raw_marketing', 'daily_campaign_performance') }}
