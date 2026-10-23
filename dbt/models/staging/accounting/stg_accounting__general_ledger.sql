select
    journal_id,
    line_number::int                       as line_number,
    journal_id || '-' || line_number       as journal_line_id,
    entry_date::date                       as entry_date,
    to_date(period || '-01', 'YYYY-MM-DD') as period_month,
    account_id::int                        as account_id,
    department,
    nullif(product, '')                    as product,
    nullif(vendor, '')                     as vendor,
    description,
    debit_usd::decimal(16, 2)              as debit_usd,
    credit_usd::decimal(16, 2)             as credit_usd,
    source                                 as journal_source,
    nullif(reference_id, '')               as reference_id
from {{ source('raw_accounting', 'general_ledger') }}
