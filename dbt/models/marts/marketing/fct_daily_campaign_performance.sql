-- Daily campaign performance enriched with campaign attributes and rate metrics.
select
    d.activity_date,
    date_trunc('month', d.activity_date)::date               as activity_month,
    extract(dow from d.activity_date) in (0, 6)              as is_weekend,
    d.campaign_id,
    c.campaign_name,
    c.channel,
    c.objective,
    c.product,
    d.impressions,
    d.clicks,
    d.spend_usd,
    d.signups,
    d.funded_accounts,
    round(100.0 * d.clicks / nullif(d.impressions, 0), 3)    as ctr_pct,
    round(d.spend_usd / nullif(d.clicks, 0), 2)              as cpc_usd,
    round(100.0 * d.signups / nullif(d.clicks, 0), 2)        as click_to_signup_pct
from {{ ref('stg_marketing__daily_performance') }} d
join {{ ref('stg_marketing__campaigns') }} c using (campaign_id)
