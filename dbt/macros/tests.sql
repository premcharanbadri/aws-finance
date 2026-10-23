{# Small generic tests so the project needs no external packages (no `dbt deps`). #}

{% test unique_combination_of_columns(model, columns) %}
    select {{ columns | join(', ') }}, count(*) as n
    from {{ model }}
    group by {{ columns | join(', ') }}
    having count(*) > 1
{% endtest %}


{# Every journal's debits equal its credits. #}
{% test journals_balance(model) %}
    select journal_id, sum(debit_usd) - sum(credit_usd) as imbalance
    from {{ model }}
    group by journal_id
    having sum(debit_usd) <> sum(credit_usd)
{% endtest %}


{# Assets = liabilities + equity every month (to the cent). #}
{% test balance_sheet_balances(model) %}
    select month, total_assets_usd, total_liabilities_usd, total_equity_usd
    from {{ model }}
    where abs(total_assets_usd - total_liabilities_usd - total_equity_usd) > 0.01
{% endtest %}
