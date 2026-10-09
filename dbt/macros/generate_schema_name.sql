{#- DuckDB: plain staging / intermediate / marts schemas.
    Athena: Glue databases <prefix>_staging, <prefix>_intermediate, <prefix>_marts, which
    infra/terraform/modules/analytics creates. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- elif target.type == 'athena' -%}
        {{ target.schema }}_{{ custom_schema_name | trim }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
