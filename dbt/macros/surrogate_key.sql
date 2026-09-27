{% macro surrogate_key(columns) -%}
    md5(concat_ws('|', {% for c in columns %}coalesce(cast({{ c }} as varchar), '<null>'){% if not loop.last %}, {% endif %}{% endfor %}))
{%- endmacro %}
