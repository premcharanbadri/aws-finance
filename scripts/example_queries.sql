-- Example queries over the dbt marts (built by `make demo-data`).

-- 1. Cost per funded account by channel (acquisition campaigns)
SELECT channel,
       SUM(acquisition_spend_usd)                                                  AS spend_usd,
       SUM(funded_accounts)                                                        AS funded,
       ROUND(SUM(acquisition_spend_usd) / NULLIF(SUM(funded_accounts), 0), 2)     AS cost_per_funded,
       ROUND(100.0 * SUM(clicks) / NULLIF(SUM(impressions), 0), 2)                AS ctr_pct
FROM marketing.channel_monthly_performance
WHERE channel <> 'organic'
GROUP BY channel
ORDER BY cost_per_funded;

-- 2. Monthly marketing spend vs. revenue
SELECT month, spend_usd, revenue_usd, new_customers, revenue_to_spend_ratio
FROM marketing.monthly_marketing_summary
ORDER BY month;

-- 3. Worst-performing acquisition campaigns
SELECT campaign_id, campaign_name, channel, budget_usd, spend_usd, pct_of_budget_spent,
       funded_accounts, cost_per_funded_account_usd
FROM marketing.dim_campaigns
WHERE objective = 'acquisition'
ORDER BY cost_per_funded_account_usd DESC
LIMIT 10;

-- 4. Liquidity: cash, runway and illustrative investable cash
SELECT * FROM accounting.liquidity_snapshot;

-- 5. Minimum-cash covenant test by month
SELECT month, ending_cash_usd, covenant_min_cash_usd, covenant_headroom_usd, is_covenant_breach
FROM accounting.cash_flow_monthly
ORDER BY month;

-- 6. Monthly P&L
SELECT month, total_revenue_usd, gross_profit_usd, marketing_expense_usd,
       operating_expenses_usd, net_income_usd
FROM accounting.income_statement_monthly
ORDER BY month;
