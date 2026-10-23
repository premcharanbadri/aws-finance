-- Month x department x account: actual vs. budget. Positive variance = unfavorable
-- for expenses (overspend) and favorable for revenue (beat plan); see is_unfavorable.
with actual as (
    select period_month as month, department, account_id,
           sum(natural_amount_usd) as actual_usd
    from {{ ref('fct_gl_entries') }}
    where account_type in ('revenue', 'expense')
    group by 1, 2, 3
),
plan as (
    select budget_month as month, department, account_id, sum(budget_usd) as budget_usd
    from {{ ref('stg_accounting__budget') }}
    group by 1, 2, 3
),
keys as (
    select month, department, account_id from actual
    union select month, department, account_id from plan
)
select
    k.month,
    k.department,
    k.account_id,
    a.account_name,
    a.account_type,
    coalesce(x.actual_usd, 0)                                         as actual_usd,
    coalesce(p.budget_usd, 0)                                         as budget_usd,
    coalesce(x.actual_usd, 0) - coalesce(p.budget_usd, 0)             as variance_usd,
    round(100.0 * (coalesce(x.actual_usd, 0) - coalesce(p.budget_usd, 0))
          / nullif(p.budget_usd, 0), 1)                               as variance_pct,
    case when a.account_type = 'expense' then coalesce(x.actual_usd, 0) > coalesce(p.budget_usd, 0)
         else coalesce(x.actual_usd, 0) < coalesce(p.budget_usd, 0) end as is_unfavorable
from keys k
join {{ ref('stg_accounting__chart_of_accounts') }} a on a.account_id = k.account_id
left join actual x on x.month = k.month and x.department = k.department and x.account_id = k.account_id
left join plan p   on p.month = k.month and p.department = k.department and p.account_id = k.account_id
