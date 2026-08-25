"""Inline Lucide SVG icons without CDN/webfonts.

Same system as schalti_sumup: the `lucide` package bundles all Lucide
SVGs and renders them inline so they inherit `currentColor`.

Usage in f-string HTML (login page):
    {icon('log-in', 'me-1')}

Usage in plain-string HTML (dashboard): token + substitute()
    <button>{{icon:trash-2:me-1}}</button>  ->  substitute(html)
"""

from __future__ import annotations

import re

import lucide as _lucide
from lucide import IconDoesNotExist

_TOKEN_RE = re.compile(r"\{\{icon:([a-z0-9-]+)(?::([a-z0-9 _:-]+))?\}\}")


def icon(name: str, cls: str = "") -> str:
    """Renders a Lucide SVG icon inline; inherits currentColor."""
    try:
        svg = _lucide._render_icon(name, None)
    except IconDoesNotExist:
        return ""
    css = f"icon {cls}".strip()
    return svg.replace("<svg ", f'<svg class="{css}" ', 1)


def substitute(html: str) -> str:
    """Replaces {{icon:name[:classes]}} tokens with inline SVGs."""
    return _TOKEN_RE.sub(lambda m: icon(m.group(1), m.group(2) or ""), html)
