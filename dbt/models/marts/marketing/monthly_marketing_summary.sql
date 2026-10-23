-- Company-level monthly marketing spend vs. revenue (revenue is from all customers
-- acquired to date, so it compounds while spend drives only new customers).
select
    month,
    sum(spend_usd)                                                   as spend_usd,
    sum(funded_accounts)                                             as paid_funded_accounts,
    sum(new_customers)                                               as new_customers,
    sum(case when channel = 'organic' then new_customers else 0 end) as organic_customers,
    sum(total_revenue_usd)                                           as revenue_usd,
    round(sum(total_revenue_usd) / nullif(sum(spend_usd), 0), 2)     as revenue_to_spend_ratio,
    round(sum(acquisition_spend_usd) / nullif(sum(funded_accounts), 0), 2) as blended_cost_per_funded_account_usd
from {{ ref('channel_monthly_performance') }}
group by 1
