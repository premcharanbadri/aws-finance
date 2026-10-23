select
    facility_id,
    lender,
    facility_type,
    gl_account_id::int                                 as gl_account_id,
    commitment_usd::decimal(16, 2)                     as commitment_usd,
    interest_rate_pct::decimal(6, 3)                   as interest_rate_pct,
    nullif(advance_rate_pct, '')::decimal(6, 2)        as advance_rate_pct,
    origination_date::date                             as origination_date,
    maturity_date::date                                as maturity_date,
    min_unrestricted_cash_usd::decimal(16, 2)          as min_unrestricted_cash_usd,
    nullif(max_utilization_pct, '')::decimal(6, 2)     as max_utilization_pct
from {{ source('raw_accounting', 'debt_facilities') }}
