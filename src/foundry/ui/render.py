"""Markdown -> HTML for reports. Run content is untrusted: no raw HTML, no images, only http(s)/relative links."""

from __future__ import annotations

from markdown_it import MarkdownIt


def _safe_link(url: str) -> bool:
    url = url.strip()
    if url.startswith("//") or "\\" in url or "%5c" in url.lower():  # protocol-relative or backslash tricks
        return False
    head = url.split("/")[0]
    return url.lower().startswith(("http://", "https://")) or ":" not in head


_md = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")
_md.validateLink = _safe_link


def render(markdown: str) -> str:
    return _md.render(markdown)
