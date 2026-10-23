-- Example queries over the marketing tables (typed columns, created by load_csv.py).

-- 1. Cost per funded account by channel (acquisition campaigns)
SELECT c.channel,
       ROUND(SUM(d.spend_usd), 0)                                            AS spend_usd,
       SUM(d.funded_accounts)                                                AS funded,
       ROUND(SUM(d.spend_usd) / NULLIF(SUM(d.funded_accounts), 0), 2)        AS cost_per_funded,
       ROUND(100.0 * SUM(d.clicks) / NULLIF(SUM(d.impressions), 0), 2)       AS ctr_pct
FROM daily_campaign_performance d
JOIN campaigns c ON c.campaign_id = d.campaign_id
WHERE c.objective = 'acquisition'
GROUP BY c.channel
ORDER BY cost_per_funded;

-- 2. Monthly marketing spend vs. revenue
WITH spend AS (
  SELECT DATE_TRUNC('month', date)::DATE AS month, SUM(spend_usd) AS spend_usd
  FROM daily_campaign_performance
  GROUP BY 1
), rev AS (
  SELECT month,
         SUM(interchange_revenue_usd + interest_revenue_usd + fee_revenue_usd) AS revenue_usd,
         SUM(new_customers) AS new_customers
  FROM monthly_revenue
  GROUP BY 1
)
SELECT s.month,
       ROUND(s.spend_usd, 0)                              AS spend_usd,
       ROUND(r.revenue_usd, 0)                            AS revenue_usd,
       r.new_customers,
       ROUND(r.revenue_usd / NULLIF(s.spend_usd, 0), 2)   AS revenue_to_spend
FROM spend s
JOIN rev r ON r.month = s.month
ORDER BY s.month;

-- 3. Worst-performing campaigns: cost per funded account and budget overrun
SELECT c.campaign_id, c.name, c.channel, c.product, c.budget_usd,
       ROUND(SUM(d.spend_usd), 0)                                            AS spend_usd,
       ROUND(100.0 * SUM(d.spend_usd) / c.budget_usd - 100, 1)               AS pct_over_budget,
       SUM(d.funded_accounts)                                                AS funded,
       ROUND(SUM(d.spend_usd) / NULLIF(SUM(d.funded_accounts), 0), 2)        AS cost_per_funded
FROM campaigns c
JOIN daily_campaign_performance d ON d.campaign_id = c.campaign_id
WHERE c.objective = 'acquisition'
GROUP BY c.campaign_id, c.name, c.channel, c.product, c.budget_usd
ORDER BY cost_per_funded DESC
LIMIT 10;
