select
    account_id::int  as account_id,
    account_name,
    account_type,
    account_subtype,
    normal_balance
from {{ source('raw_accounting', 'chart_of_accounts') }}
