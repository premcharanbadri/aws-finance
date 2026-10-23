-- AP invoices with payment timeliness, duplicate detection and aging as of var('as_of_date').
with inv as (
    select i.*,
           count(*) over (partition by vendor, vendor_invoice_number) as copies,
           row_number() over (partition by vendor, vendor_invoice_number order by invoice_id) as copy_number
    from {{ ref('stg_accounting__ap_invoices') }} i
),
asof as (select '{{ var("as_of_date") }}'::date as as_of_date)
select
    inv.invoice_id,
    inv.vendor,
    inv.vendor_invoice_number,
    inv.gl_account_id,
    a.account_name                                               as gl_account_name,
    inv.department,
    inv.description,
    inv.invoice_date,
    inv.due_date,
    inv.paid_date,
    inv.status,
    inv.amount_usd,
    inv.paid_date is null                                        as is_open,
    datediff(day, inv.invoice_date, inv.paid_date)               as days_to_pay,
    inv.paid_date > inv.due_date                                 as was_paid_late,
    inv.copies > 1                                               as is_potential_duplicate,
    inv.copy_number > 1                                          as is_duplicate_copy,
    case when inv.paid_date is null
         then datediff(day, inv.due_date, asof.as_of_date) end   as days_past_due,
    case
        when inv.paid_date is not null                             then 'paid'
        when asof.as_of_date <= inv.due_date                       then 'current'
        when datediff(day, inv.due_date, asof.as_of_date) <= 30    then '1-30'
        when datediff(day, inv.due_date, asof.as_of_date) <= 60    then '31-60'
        when datediff(day, inv.due_date, asof.as_of_date) <= 90    then '61-90'
        else '90+'
    end                                                          as aging_bucket,
    asof.as_of_date
from inv
cross join asof
join {{ ref('stg_accounting__chart_of_accounts') }} a on a.account_id = inv.gl_account_id
