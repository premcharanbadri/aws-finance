select
    invoice_id,
    vendor,
    vendor_invoice_number,
    gl_account_id::int           as gl_account_id,
    department,
    invoice_date::date           as invoice_date,
    due_date::date               as due_date,
    amount_usd::decimal(14, 2)   as amount_usd,
    nullif(paid_date, '')::date  as paid_date,
    status,
    description
from {{ source('raw_accounting', 'ap_invoices') }}
