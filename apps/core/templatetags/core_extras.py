"""
Shared template tags / filters.

``markdownify`` renders stored Markdown to HTML for display. Raw embedded HTML
is escaped (``safe_mode="escape"``) so user-authored Markdown cannot inject
markup — important because Tasks/Tests/POC descriptions are user content.
"""

import re

import markdown2
from django import template
from django.utils.html import urlize as django_urlize
from django.utils.safestring import mark_safe

register = template.Library()

_URLIZED_LINK_RE = re.compile(r'<a href="([^"]*)"([^>]*)>(.*?)</a>')

_MARKDOWN_EXTRAS = [
    "fenced-code-blocks",
    "tables",
    "strike",
    "cuddled-lists",
    "break-on-newline",
]


@register.filter(name="get_item")
def get_item(mapping, key):
    """Dict lookup by a variable key in templates: ``{{ d|get_item:key }}``.

    Tries ``key`` as given, then as a string, then as an int — template
    callers don't always agree on whether a PK is an int or the string a
    rendered form field gives back (e.g. ``BoundWidget.data.value``).
    """
    try:
        if key in mapping:
            return mapping[key]
        skey = str(key)
        if skey in mapping:
            return mapping[skey]
        return mapping.get(int(key))
    except (AttributeError, TypeError, ValueError):
        return None


@register.filter(name="linkify")
def linkify(value):
    """Like the builtin ``urlize``, but the link reads as a link: blue,
    underlined, with a small link icon right before it (comment text is
    plain, not Markdown, so bare URLs otherwise render as plain text).
    """
    if not value:
        return ""
    html = django_urlize(str(value), autoescape=True)

    def _style_link(match):
        href, text = match.group(1), match.group(3)
        return (
            '<i data-lucide="link" class="inline-block w-3.5 h-3.5 align-text-bottom mr-0.5 text-brand-dark"></i>'
            f'<a href="{href}" class="text-brand-dark underline hover:text-brand" '
            'target="_blank" rel="noopener noreferrer">'
            f"{text}</a>"
        )

    return mark_safe(_URLIZED_LINK_RE.sub(_style_link, html))


@register.filter(name="markdownify")
def markdownify(value):
    """Render a Markdown string to sanitised HTML."""
    if not value:
        return ""
    html = markdown2.markdown(
        str(value), extras=_MARKDOWN_EXTRAS, safe_mode="escape"
    )
    return mark_safe(html)
