select
    "month"::date                as budget_month,
    department,
    account_id::int              as account_id,
    budget_usd::decimal(14, 2)   as budget_usd
from {{ source('raw_accounting', 'budget') }}
