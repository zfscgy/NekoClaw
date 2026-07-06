"""Render-aware web page fetcher.

``web_fetch`` drives a browser session from the shared pool, waits for the
page to finish rendering, strips noise nodes (``<script>``, ``<style>``,
``<img>``) from the resulting DOM and returns either the cleaned HTML or a
markdown rendering of it.
"""

from __future__ import annotations

import typing as t

from lightsear._encoding import decode_html_body
from lightsear.runtime import ensure_chromium_alive, get_pool

FetchMode = t.Literal["markdown", "html"]


def web_fetch(
    url: str,
    *,
    mode: FetchMode = "markdown",
    wait: int = 8_000,
) -> str:
    """Fetch a URL and return readable content.

    Call :func:`lightsear.initialize_pool` once before the first use to set up
    the browser session pool.

    :param url: URL to fetch.
    :param mode: ``'markdown'`` (default) returns markdown rendered from the
        cleaned DOM — compact and cheap. ``'html'`` returns the cleaned HTML
        directly, which is noticeably more verbose and costs more tokens; only
        ask for it when you specifically need raw markup.
    :param wait: Extra milliseconds to wait for JS rendering before scraping.
    """
    if mode not in {"markdown", "html"}:
        raise ValueError("mode must be 'markdown' or 'html'")

    ensure_chromium_alive()
    response = get_pool().submit(_run_web_fetch, url, wait).result()
    html_text = decode_html_body(response.body)
    cleaned_html = _strip_noise_nodes(html_text)
    if mode == "markdown":
        # Imported lazily so search-only deployments don't require markdownify.
        from markdownify import markdownify as to_markdown

        return to_markdown(cleaned_html)
    return cleaned_html


def _run_web_fetch(session: t.Any, url: str, wait: int) -> t.Any:
    return session.fetch(url, wait=wait)


def _strip_noise_nodes(html_text: str) -> str:
    from lxml import html

    root = html.fromstring(html_text)
    for node in root.xpath("//script|//style|//img"):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return html.tostring(root, encoding="unicode", method="html")


__all__ = ["FetchMode", "web_fetch"]
