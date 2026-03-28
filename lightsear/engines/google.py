# Logic adapted from SearXNG searx/engines/google.py (AGPL-3.0-or-later).
# Simplified: fixed locale, no traits / paging options.

from __future__ import annotations

import logging
from urllib.parse import urlencode, urlparse

from lxml import html

from lightsear._xpath import eval_xpath, eval_xpath_getindex, eval_xpath_list, extract_text
from lightsear.exceptions import CaptchaError, LightsearError
from lightsear.models import SearchResult

logger = logging.getLogger(__name__)


def _detect_google_sorry(resp) -> None:
    parsed = urlparse(resp.url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host == "sorry.google.com" or path.startswith("/sorry"):
        raise CaptchaError("Google returned sorry/captcha page")


def _build_search_url(query: str, start: int = 0) -> str:
    params = {
        "q": query,
        "hl": "en-US",
        "ie": "utf8",
        "oe": "utf8",
        "filter": "0",
        "start": start,
    }
    return f"https://www.google.com/search?{urlencode(params)}"


def _parse_google_html(text: str | bytes, final_url: str) -> list[SearchResult]:
    parsed = urlparse(final_url)
    if (parsed.hostname or "").lower() == "sorry.google.com" or (parsed.path or "").startswith("/sorry"):
        raise CaptchaError("Google returned sorry/captcha page")

    dom = html.fromstring(text)
    out: list[SearchResult] = []

    # Each organic result in the rendered DOM has an <h3> inside #rso.
    # The <h3> is always wrapped by an <a href="https://..."> a few levels up.
    for h3 in eval_xpath_list(dom, '//div[@id="rso"]//h3'):
        try:
            title = extract_text(h3) or ""
            if not title:
                continue

            # Nearest ancestor <a> that carries the real destination URL
            a_tag = eval_xpath_getindex(
                h3, "ancestor::a[starts-with(@href,'http')][1]", 0, None
            )
            if a_tag is None:
                # Fallback: sibling/cousin <a> inside the same parent container
                a_tag = eval_xpath_getindex(
                    h3, "parent::*/a[starts-with(@href,'http')]", 0, None
                )
            if a_tag is None:
                continue

            href = a_tag.get("href", "")
            if not href.startswith("http"):
                continue

            # Snippet: walk up from the <a> looking for a div with description text.
            # Google uses several class names; try the most stable data-* attributes first.
            content = ""
            node = a_tag.getparent()
            for _ in range(6):
                if node is None:
                    break
                snip = eval_xpath(
                    node,
                    './/div[@data-sncf] | .//div[contains(@class,"VwiC3b")]'
                    ' | .//div[contains(@class,"s")]//span[@class="st"]',
                )
                if snip:
                    content = extract_text(snip[0]) or ""
                    break
                node = node.getparent()

            out.append(SearchResult(title=title, content=content, url=href, sources="google"))
        except (ValueError, IndexError, TypeError) as e:
            logger.debug("skip google result: %s", e)

    return out


def search_google(client, keyword: str) -> list[SearchResult]:
    url = _build_search_url(keyword, start=0)
    resp = client.fetch(
        url,
        google_search=False,
        network_idle=True,
        # Block until the organic-results container is present in the DOM
        wait_selector="#rso",
        wait_selector_state="attached",
    )
    if resp.status >= 400:
        raise LightsearError(f"Google returned HTTP {resp.status}")
    _detect_google_sorry(resp)
    return _parse_google_html(resp.body, resp.url)
