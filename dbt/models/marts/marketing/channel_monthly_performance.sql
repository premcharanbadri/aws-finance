-- Month x acquisition channel: spend, funnel, unit economics, revenue and retention.
-- 'organic' appears with zero spend.
with spend as (
    select activity_month as month, channel,
           sum(impressions) as impressions, sum(clicks) as clicks, sum(spend_usd) as spend_usd,
           sum(signups) as signups,
           sum(case when objective = 'acquisition' then funded_accounts else 0 end) as funded_accounts,
           sum(case when objective = 'acquisition' then spend_usd else 0 end)       as acquisition_spend_usd
    from {{ ref('fct_daily_campaign_performance') }}
    group by 1, 2
),
customers as (
    select signup_month as month, acquisition_channel as channel,
           count(*) as new_customers,
           sum(case when is_active_90d then 1 else 0 end) as active_90d_customers,
           sum(first_funding_amount_usd) as first_funding_usd
    from {{ ref('dim_customers') }}
    group by 1, 2
),
revenue as (
    select revenue_month as month, channel,
           sum(interchange_revenue_usd) as interchange_revenue_usd,
           sum(interest_revenue_usd)    as interest_revenue_usd,
           sum(fee_revenue_usd)         as fee_revenue_usd
    from {{ ref('stg_marketing__monthly_revenue') }}
    group by 1, 2
),
keys as (
    select month, channel from spend
    union select month, channel from customers
    union select month, channel from revenue
)
select
    k.month,
    k.channel,
    coalesce(s.spend_usd, 0)                      as spend_usd,
    coalesce(s.acquisition_spend_usd, 0)          as acquisition_spend_usd,
    coalesce(s.impressions, 0)                    as impressions,
    coalesce(s.clicks, 0)                         as clicks,
    coalesce(s.signups, 0)                        as signups,
    coalesce(s.funded_accounts, 0)                as funded_accounts,
    coalesce(c.new_customers, 0)                  as new_customers,
    coalesce(c.active_90d_customers, 0)           as active_90d_customers,
    coalesce(c.first_funding_usd, 0)              as first_funding_usd,
    coalesce(r.interchange_revenue_usd, 0)        as interchange_revenue_usd,
    coalesce(r.interest_revenue_usd, 0)           as interest_revenue_usd,
    coalesce(r.fee_revenue_usd, 0)                as fee_revenue_usd,
    coalesce(r.interchange_revenue_usd, 0) + coalesce(r.interest_revenue_usd, 0)
        + coalesce(r.fee_revenue_usd, 0)          as total_revenue_usd,
    round(100.0 * s.clicks / nullif(s.impressions, 0), 3)                   as ctr_pct,
    round(s.acquisition_spend_usd / nullif(s.funded_accounts, 0), 2)        as cost_per_funded_account_usd,
    round(100.0 * c.active_90d_customers / nullif(c.new_customers, 0), 1)   as active_90d_rate_pct
from keys k
left join spend s     on s.month = k.month and s.channel = k.channel
left join customers c on c.month = k.month and c.channel = k.channel
left join revenue r   on r.month = k.month and r.channel = k.channel
