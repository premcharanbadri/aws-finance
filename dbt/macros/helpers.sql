{# Use the custom schema name as-is (staging / marketing / accounting) instead of
   dbt's default "<target_schema>_<custom>" so agents see clean schema names. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}


{# Table-level grants come from the +grants config; schema USAGE has to be granted
   separately. Skipped (with a log line) if the advisor user doesn't exist yet. #}
{% macro grant_mart_schema_usage() %}
    {% if execute %}
        {% set user = var('advisor_user') %}
        {% set exists = run_query("select 1 from pg_user where usename = '" ~ user ~ "'") %}
        {% if exists.rows | length > 0 %}
            {% do run_query("grant usage on schema marketing, accounting to " ~ user) %}
            {% do log("granted usage on marketing, accounting to " ~ user, info=true) %}
        {% else %}
            {% do log("advisor user " ~ user ~ " not found; run scripts/setup_advisor_user.py", info=true) %}
        {% endif %}
    {% endif %}
{% endmacro %}
