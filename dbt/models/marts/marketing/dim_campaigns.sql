-- One row per campaign with lifetime delivery, funnel and budget pacing.
with perf as (
    select
        campaign_id,
        sum(impressions)      as impressions,
        sum(clicks)           as clicks,
        sum(spend_usd)        as spend_usd,
        sum(signups)          as signups,
        sum(funded_accounts)  as funded_accounts,
        min(activity_date)    as first_active_date,
        max(activity_date)    as last_active_date
    from {{ ref('stg_marketing__daily_performance') }}
    group by 1
)
select
    c.campaign_id,
    c.campaign_name,
    c.channel,
    c.objective,
    c.product,
    c.start_date,
    c.end_date,
    datediff(day, c.start_date, c.end_date) + 1                        as flight_days,
    c.budget_usd,
    coalesce(p.spend_usd, 0)                                            as spend_usd,
    round(100.0 * coalesce(p.spend_usd, 0) / nullif(c.budget_usd, 0), 1) as pct_of_budget_spent,
    coalesce(p.spend_usd, 0) > c.budget_usd * 1.05                      as is_over_budget,
    p.impressions,
    p.clicks,
    p.signups,
    p.funded_accounts,
    round(100.0 * p.clicks / nullif(p.impressions, 0), 3)               as ctr_pct,
    round(p.spend_usd / nullif(p.clicks, 0), 2)                         as cpc_usd,
    round(p.spend_usd / nullif(p.signups, 0), 2)                        as cost_per_signup_usd,
    round(p.spend_usd / nullif(p.funded_accounts, 0), 2)                as cost_per_funded_account_usd,
    round(100.0 * p.funded_accounts / nullif(p.signups, 0), 1)          as signup_to_funded_pct
from {{ ref('stg_marketing__campaigns') }} c
left join perf p using (campaign_id)
