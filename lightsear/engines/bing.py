# Logic adapted from SearXNG searx/engines/bing.py (AGPL-3.0-or-later).

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlencode, urlparse

from lxml import html

from lightsear._xpath import eval_xpath, eval_xpath_getindex, eval_xpath_list, extract_text
from lightsear.exceptions import LightsearError
from lightsear.models import SearchResult

BING_SEARCH = "https://www.bing.com/search"


def _parse_bing_html(text: str | bytes) -> list[SearchResult]:
    dom = html.fromstring(text)
    out: list[SearchResult] = []

    for item in eval_xpath_list(dom, '//ol[@id="b_results"]/li[contains(@class, "b_algo")]'):
        link = eval_xpath_getindex(item, ".//h2/a", 0, None)
        if link is None:
            continue

        href = link.attrib.get("href", "")
        title = extract_text(link) or ""

        if not href or not title:
            continue

        if href.startswith("https://www.bing.com/ck/a?"):
            qs = parse_qs(urlparse(href).query)
            u_values = qs.get("u")
            if u_values:
                u_val = u_values[0]
                if u_val.startswith("a1"):
                    encoded = u_val[2:]
                    encoded += "=" * (-len(encoded) % 4)
                    href = base64.urlsafe_b64decode(encoded).decode("utf-8", errors="replace")

        content_els = eval_xpath(item, ".//p")
        for p in content_els:
            for icon in p.xpath('.//span[@class="algoSlug_icon"]'):
                parent = icon.getparent()
                if parent is not None:
                    parent.remove(icon)
        content = extract_text(content_els) or ""

        out.append(SearchResult(title=title, content=content, url=href, sources="bing"))

    return out


def search_bing(client, keyword: str) -> list[SearchResult]:
    query = urlencode({"q": keyword, "adlt": "off"})
    url = f"{BING_SEARCH}?{query}"
    resp = client.fetch(
        url,
        network_idle=True,
        # Wait for the main results list before parsing
        wait_selector="#b_results",
        wait_selector_state="attached",
    )
    if resp.status >= 400:
        raise LightsearError(f"Bing returned HTTP {resp.status}")
    return _parse_bing_html(resp.body)
