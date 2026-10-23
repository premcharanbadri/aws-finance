-- Monthly income statement (wide). All values in USD, expenses positive.
with b as (select * from {{ ref('monthly_account_balances') }})
select
    month,
    sum(case when account_type = 'revenue' then period_activity_usd else 0 end)               as total_revenue_usd,
    sum(case when account_id = 4000 then period_activity_usd else 0 end)                      as interchange_revenue_usd,
    sum(case when account_id = 4100 then period_activity_usd else 0 end)                      as interest_revenue_usd,
    sum(case when account_id = 4200 then period_activity_usd else 0 end)                      as fee_revenue_usd,
    sum(case when account_subtype = 'cost_of_revenue' then period_activity_usd else 0 end)    as cost_of_revenue_usd,
    sum(case when account_type = 'revenue' then period_activity_usd else 0 end)
      - sum(case when account_subtype = 'cost_of_revenue' then period_activity_usd else 0 end) as gross_profit_usd,
    sum(case when account_id in (6000, 6010) then period_activity_usd else 0 end)             as payroll_expense_usd,
    sum(case when account_id in (6100, 6110) then period_activity_usd else 0 end)             as marketing_expense_usd,
    sum(case when account_subtype = 'operating_expense' then period_activity_usd else 0 end)  as operating_expenses_usd,
    sum(case when account_type = 'revenue' then period_activity_usd else 0 end)
      - sum(case when account_subtype in ('cost_of_revenue', 'operating_expense') then period_activity_usd else 0 end)
                                                                                              as operating_income_usd,
    sum(case when account_subtype = 'interest_expense' then period_activity_usd else 0 end)   as interest_expense_usd,
    sum(case when account_type = 'revenue' then period_activity_usd else 0 end)
      - sum(case when account_type = 'expense' then period_activity_usd else 0 end)           as net_income_usd
from b
group by 1
