-- Month x account: activity and month-end balance (balance = cumulative activity).
-- Balance sheet accounts use ending_balance; P&L accounts use period_activity.
with months as (
    -- every month has GL activity (payroll, revenue), so this is a complete spine
    select distinct period_month as month from {{ ref('stg_accounting__general_ledger') }}
),
activity as (
    select period_month, account_id,
           sum(debit_usd) as debits_usd, sum(credit_usd) as credits_usd,
           sum(natural_amount_usd) as period_activity_usd
    from {{ ref('fct_gl_entries') }}
    group by 1, 2
),
grid as (
    select m.month, a.account_id, a.account_name, a.account_type, a.account_subtype
    from months m cross join {{ ref('stg_accounting__chart_of_accounts') }} a
)
select
    g.month,
    g.account_id,
    g.account_name,
    g.account_type,
    g.account_subtype,
    coalesce(x.debits_usd, 0)            as debits_usd,
    coalesce(x.credits_usd, 0)           as credits_usd,
    coalesce(x.period_activity_usd, 0)   as period_activity_usd,
    sum(coalesce(x.period_activity_usd, 0)) over (
        partition by g.account_id order by g.month rows unbounded preceding
    )                                    as ending_balance_usd
from grid g
left join activity x on x.period_month = g.month and x.account_id = g.account_id
