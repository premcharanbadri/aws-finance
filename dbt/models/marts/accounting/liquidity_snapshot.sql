-- One-row liquidity snapshot as of var('as_of_date'): what's on hand, what's committed,
-- and an illustrative "investable cash" figure. The reserve policy is a planning
-- assumption (6 months of trailing operating burn), not advice.
{% set as_of = "'" ~ var('as_of_date') ~ "'::date" %}
with latest as (
    select * from {{ ref('cash_flow_monthly') }}
    where month = date_trunc('month', {{ as_of }})::date
),
burn as (
    select avg(operating_burn_usd) as avg_monthly_burn_3m_usd
    from {{ ref('cash_flow_monthly') }}
    where month > dateadd(month, -3, date_trunc('month', {{ as_of }}))::date
),
opex as (
    select avg(operating_expenses_usd + cost_of_revenue_usd + interest_expense_usd) as avg_monthly_costs_3m_usd
    from {{ ref('income_statement_monthly') }}
    where month > dateadd(month, -3, date_trunc('month', {{ as_of }}))::date
),
ap as (
    select sum(amount_usd) as open_ap_usd,
           sum(case when aging_bucket not in ('paid', 'current') then amount_usd else 0 end) as past_due_ap_usd
    from {{ ref('fct_ap_invoices') }} where is_open
),
tax as (
    select sum(amount_usd) as taxes_due_next_180d_usd
    from {{ ref('tax_calendar') }}
    where paid_date is null and due_date <= dateadd(day, 180, {{ as_of }})
),
restricted as (
    select ending_balance_usd as restricted_cash_usd
    from {{ ref('monthly_account_balances') }}
    where account_id = 1010 and month = date_trunc('month', {{ as_of }})::date
)
select
    {{ as_of }}                                             as as_of_date,
    l.ending_cash_usd                                       as unrestricted_cash_usd,
    r.restricted_cash_usd,
    b.avg_monthly_burn_3m_usd,
    o.avg_monthly_costs_3m_usd,
    round(l.ending_cash_usd / nullif(b.avg_monthly_burn_3m_usd, 0), 1) as runway_months,
    l.covenant_min_cash_usd,
    a.open_ap_usd,
    a.past_due_ap_usd,
    coalesce(t.taxes_due_next_180d_usd, 0)                  as taxes_due_next_180d_usd,
    6 * b.avg_monthly_burn_3m_usd                           as operating_reserve_6m_usd,
    greatest(
        l.ending_cash_usd
        - l.covenant_min_cash_usd
        - 6 * b.avg_monthly_burn_3m_usd
        - a.open_ap_usd
        - coalesce(t.taxes_due_next_180d_usd, 0),
        0
    )                                                       as illustrative_investable_cash_usd
from latest l
cross join burn b
cross join opex o
cross join ap a
cross join tax t
cross join restricted r
