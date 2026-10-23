select
    obligation_id,
    tax_type,
    jurisdiction,
    tax_period,
    due_date::date               as due_date,
    amount_usd::decimal(14, 2)   as amount_usd,
    nullif(paid_date, '')::date  as paid_date,
    penalty_usd::decimal(14, 2)  as penalty_usd,
    status,
    accrued_in_gl = 'true'       as accrued_in_gl
from {{ source('raw_accounting', 'tax_obligations') }}
