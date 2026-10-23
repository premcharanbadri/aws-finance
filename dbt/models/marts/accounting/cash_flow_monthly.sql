-- Monthly cash flow from operating cash (account 1000), with burn and the venture-debt
-- minimum unrestricted cash covenant test at each month-end.
with flows as (
    select period_month as month,
           sum(case when cash_flow_category = 'operating' then net_debit_usd else 0 end) as operating_cash_flow_usd,
           sum(case when cash_flow_category = 'investing' then net_debit_usd else 0 end) as investing_cash_flow_usd,
           sum(case when cash_flow_category = 'financing' then net_debit_usd else 0 end) as financing_cash_flow_usd,
           sum(case when cash_flow_category <> 'opening_balance' then net_debit_usd else 0 end) as net_cash_change_usd
    from {{ ref('fct_gl_entries') }}
    where account_id = 1000
    group by 1
),
bal as (
    select month, ending_balance_usd as ending_cash_usd
    from {{ ref('monthly_account_balances') }}
    where account_id = 1000
),
covenant as (
    select min_unrestricted_cash_usd
    from {{ ref('stg_accounting__debt_facilities') }}
    where facility_type = 'venture_debt'
)
select
    b.month,
    b.ending_cash_usd - f.net_cash_change_usd                as beginning_cash_usd,
    f.operating_cash_flow_usd,
    f.investing_cash_flow_usd,
    f.financing_cash_flow_usd,
    f.net_cash_change_usd,
    b.ending_cash_usd,
    greatest(-f.operating_cash_flow_usd, 0)                  as operating_burn_usd,
    c.min_unrestricted_cash_usd                              as covenant_min_cash_usd,
    b.ending_cash_usd - c.min_unrestricted_cash_usd          as covenant_headroom_usd,
    b.ending_cash_usd < c.min_unrestricted_cash_usd          as is_covenant_breach
from bal b
join flows f on f.month = b.month
cross join covenant c
