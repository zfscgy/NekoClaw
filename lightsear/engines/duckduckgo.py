# Logic adapted from SearXNG searx/engines/duckduckgo.py (AGPL-3.0-or-later).
# Fetches the HTML endpoint via GET (browser navigation).

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse

import lxml.html

from lightsear._xpath import eval_xpath, eval_xpath_getindex, eval_xpath_list, extract_text
from lightsear.exceptions import CaptchaError, LightsearError
from lightsear.models import SearchResult

DDG_HTML_URL = "https://html.duckduckgo.com/html/"


def _is_ddg_captcha(doc: lxml.html.HtmlElement) -> bool:
    return bool(eval_xpath(doc, "//form[@id='challenge-form']"))


def _parse_ddg_html(text: str | bytes) -> list[SearchResult]:
    doc = lxml.html.fromstring(text)
    if _is_ddg_captcha(doc):
        raise CaptchaError("DuckDuckGo challenge/CAPTCHA")

    out: list[SearchResult] = []
    for div_result in eval_xpath_list(doc, '//div[@id="links"]/div[contains(@class, "web-result")]'):
        titles = eval_xpath(div_result, ".//h2/a")
        hrefs = eval_xpath(div_result, ".//h2/a/@href")
        if not hrefs:
            continue
        title = extract_text(titles) or ""
        raw_href = hrefs[0]
        parsed = urlparse(raw_href if raw_href.startswith("http") else "https:" + raw_href)
        uddg = parse_qs(parsed.query).get("uddg", [None])[0]
        url = uddg if uddg else raw_href
        snippet_el = eval_xpath_getindex(div_result, './/a[contains(@class, "result__snippet")]', 0, [])
        content = extract_text(snippet_el) or ""
        if title and url:
            out.append(SearchResult(title=title, content=content, url=url, sources="duckduckgo"))
    return out


def search_duckduckgo(client, keyword: str) -> list[SearchResult]:
    if len(keyword) >= 500:
        return []

    url = f"{DDG_HTML_URL}?{urlencode({'q': keyword, 'kl': 'wt-wt'})}"
    resp = client.fetch(
        url,
        google_search=False,
        network_idle=True,
        # Wait for the results container before parsing
        wait_selector="div#links",
        wait_selector_state="attached",
    )
    if resp.status >= 400:
        raise LightsearError(f"DuckDuckGo returned HTTP {resp.status}")
    return _parse_ddg_html(resp.body)
