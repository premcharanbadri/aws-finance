-- Debt facilities with outstanding balance, utilization and covenant tests per month.
with bal as (
    select month, account_id, ending_balance_usd
    from {{ ref('monthly_account_balances') }}
),
cash as (select month, ending_cash_usd from {{ ref('cash_flow_monthly') }})
select
    b.month,
    f.facility_id,
    f.lender,
    f.facility_type,
    f.commitment_usd,
    b.ending_balance_usd                                           as outstanding_usd,
    f.commitment_usd - b.ending_balance_usd                        as undrawn_usd,
    round(100.0 * b.ending_balance_usd / nullif(f.commitment_usd, 0), 1) as utilization_pct,
    f.max_utilization_pct,
    coalesce(100.0 * b.ending_balance_usd / nullif(f.commitment_usd, 0) > f.max_utilization_pct, false)
                                                                   as is_utilization_breach,
    f.interest_rate_pct,
    round(b.ending_balance_usd * f.interest_rate_pct / 100 / 12, 2) as monthly_interest_usd,
    f.maturity_date,
    datediff(month, b.month, f.maturity_date)                      as months_to_maturity,
    f.min_unrestricted_cash_usd,
    c.ending_cash_usd,
    c.ending_cash_usd < f.min_unrestricted_cash_usd                as is_min_cash_breach
from {{ ref('stg_accounting__debt_facilities') }} f
join bal b on b.account_id = f.gl_account_id
join cash c on c.month = b.month
