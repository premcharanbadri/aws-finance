-- Example queries over the marketing tables (all columns are VARCHAR, hence the CASTs).

-- 1. Cost per funded account by channel
SELECT c.channel,
       ROUND(SUM(CAST(d.spend_usd AS DECIMAL(14,2))), 0)                        AS spend_usd,
       SUM(CAST(d.funded_accounts AS INT))                                       AS funded,
       ROUND(SUM(CAST(d.spend_usd AS DECIMAL(14,2)))
             / NULLIF(SUM(CAST(d.funded_accounts AS INT)), 0), 2)                AS cost_per_funded,
       ROUND(100.0 * SUM(CAST(d.clicks AS BIGINT))
             / NULLIF(SUM(CAST(d.impressions AS BIGINT)), 0), 2)                 AS ctr_pct
FROM daily_campaign_performance d
JOIN campaigns c ON c.campaign_id = d.campaign_id
WHERE c.objective = 'acquisition'
GROUP BY c.channel
ORDER BY cost_per_funded;

-- 2. Monthly marketing spend vs. revenue
WITH spend AS (
  SELECT SUBSTRING(date, 1, 7) AS month, SUM(CAST(spend_usd AS DECIMAL(14,2))) AS spend_usd
  FROM daily_campaign_performance GROUP BY 1
), rev AS (
  SELECT SUBSTRING(month, 1, 7) AS month,
         SUM(CAST(interchange_revenue_usd AS DECIMAL(14,2))
           + CAST(interest_revenue_usd AS DECIMAL(14,2))
           + CAST(fee_revenue_usd AS DECIMAL(14,2))) AS revenue_usd,
         SUM(CAST(new_customers AS INT)) AS new_customers
  FROM monthly_revenue GROUP BY 1
)
SELECT s.month, ROUND(s.spend_usd, 0) AS spend_usd, ROUND(r.revenue_usd, 0) AS revenue_usd,
       r.new_customers, ROUND(r.revenue_usd / NULLIF(s.spend_usd, 0), 2) AS revenue_to_spend
FROM spend s JOIN rev r ON r.month = s.month
ORDER BY s.month;

-- 3. Worst-performing campaigns: cost per funded account and budget overrun
SELECT c.campaign_id, c.name, c.channel, c.product,
       CAST(c.budget_usd AS DECIMAL(14,2))                                       AS budget_usd,
       ROUND(SUM(CAST(d.spend_usd AS DECIMAL(14,2))), 0)                         AS spend_usd,
       ROUND(100.0 * SUM(CAST(d.spend_usd AS DECIMAL(14,2)))
             / CAST(c.budget_usd AS DECIMAL(14,2)) - 100, 1)                     AS pct_over_budget,
       SUM(CAST(d.funded_accounts AS INT))                                       AS funded,
       ROUND(SUM(CAST(d.spend_usd AS DECIMAL(14,2)))
             / NULLIF(SUM(CAST(d.funded_accounts AS INT)), 0), 2)                AS cost_per_funded
FROM campaigns c
JOIN daily_campaign_performance d ON d.campaign_id = c.campaign_id
WHERE c.objective = 'acquisition'
GROUP BY c.campaign_id, c.name, c.channel, c.product, c.budget_usd
ORDER BY cost_per_funded DESC NULLS FIRST
LIMIT 10;
