-- Tax obligations with lateness and days until due as of var('as_of_date').
select
    t.obligation_id,
    t.tax_type,
    t.jurisdiction,
    t.tax_period,
    t.due_date,
    t.amount_usd,
    t.paid_date,
    t.penalty_usd,
    t.status,
    t.accrued_in_gl,
    case when t.paid_date is not null then datediff(day, t.due_date, t.paid_date) end  as days_paid_after_due,
    case when t.paid_date is null then datediff(day, '{{ var("as_of_date") }}'::date, t.due_date) end
                                                                                       as days_until_due,
    t.paid_date is null and t.due_date < '{{ var("as_of_date") }}'::date              as is_overdue
from {{ ref('stg_accounting__tax_obligations') }} t
