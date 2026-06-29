"""
Shared template tags / filters.

``markdownify`` renders stored Markdown to HTML for display. Raw embedded HTML
is escaped (``safe_mode="escape"``) so user-authored Markdown cannot inject
markup — important because Tasks/Tests/POC descriptions are user content.
"""

import markdown2
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_MARKDOWN_EXTRAS = [
    "fenced-code-blocks",
    "tables",
    "strike",
    "cuddled-lists",
    "break-on-newline",
]


@register.filter(name="get_item")
def get_item(mapping, key):
    """Dict lookup by a variable key in templates: ``{{ d|get_item:key }}``."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter(name="markdownify")
def markdownify(value):
    """Render a Markdown string to sanitised HTML."""
    if not value:
        return ""
    html = markdown2.markdown(
        str(value), extras=_MARKDOWN_EXTRAS, safe_mode="escape"
    )
    return mark_safe(html)
