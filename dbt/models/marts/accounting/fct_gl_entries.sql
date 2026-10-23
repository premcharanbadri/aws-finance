-- GL journal lines with account metadata, signed and "natural" amounts, and a
-- cash-flow classification for lines that touch operating cash (account 1000).
select
    g.journal_line_id,
    g.journal_id,
    g.line_number,
    g.entry_date,
    g.period_month,
    g.account_id,
    a.account_name,
    a.account_type,
    a.account_subtype,
    g.department,
    g.product,
    g.vendor,
    g.description,
    g.journal_source,
    g.reference_id,
    g.debit_usd,
    g.credit_usd,
    g.debit_usd - g.credit_usd                                   as net_debit_usd,
    -- positive in the account's normal direction (revenue/liabilities as positive credits)
    case when a.normal_balance = 'debit' then g.debit_usd - g.credit_usd
         else g.credit_usd - g.debit_usd end                     as natural_amount_usd,
    case
        when g.account_id <> 1000                                   then null
        when g.journal_source = 'opening'                           then 'opening_balance'
        when g.journal_source = 'loans'                             then 'investing'
        when g.journal_source = 'financing'
             and g.description not ilike '%interest%'               then 'financing'
        else 'operating'
    end                                                          as cash_flow_category
from {{ ref('stg_accounting__general_ledger') }} g
join {{ ref('stg_accounting__chart_of_accounts') }} a using (account_id)
