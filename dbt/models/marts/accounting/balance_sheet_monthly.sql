-- Monthly balance sheet (wide), month-end balances. Contra-assets reduce assets.
with b as (select * from {{ ref('monthly_account_balances') }}),
pnl as (
    -- current-period earnings roll into equity so the sheet balances every month
    select month, sum(sum(case when account_type = 'revenue' then period_activity_usd
                               when account_type = 'expense' then -period_activity_usd else 0 end))
                  over (order by month rows unbounded preceding) as cumulative_net_income_usd
    from b group by month
)
select
    b.month,
    sum(case when b.account_id = 1000 then b.ending_balance_usd else 0 end)            as cash_operating_usd,
    sum(case when b.account_id = 1010 then b.ending_balance_usd else 0 end)            as cash_restricted_usd,
    sum(case when b.account_id = 1200 then b.ending_balance_usd else 0 end)
      - sum(case when b.account_id = 1210 then b.ending_balance_usd else 0 end)        as loans_receivable_net_usd,
    sum(case when b.account_type = 'asset' and b.account_subtype <> 'contra_asset' then b.ending_balance_usd else 0 end)
      - sum(case when b.account_subtype = 'contra_asset' then b.ending_balance_usd else 0 end)
                                                                                       as total_assets_usd,
    sum(case when b.account_id = 2000 then b.ending_balance_usd else 0 end)            as accounts_payable_usd,
    sum(case when b.account_id = 2200 then b.ending_balance_usd else 0 end)            as taxes_payable_usd,
    sum(case when b.account_subtype = 'debt' then b.ending_balance_usd else 0 end)     as total_debt_usd,
    sum(case when b.account_type = 'liability' then b.ending_balance_usd else 0 end)   as total_liabilities_usd,
    sum(case when b.account_type = 'equity' then b.ending_balance_usd else 0 end)
      + max(p.cumulative_net_income_usd)                                               as total_equity_usd
from b
join pnl p on p.month = b.month
group by 1
