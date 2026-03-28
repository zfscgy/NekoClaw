# Logic adapted from SearXNG searx/engines/baidu.py (AGPL-3.0-or-later).

from __future__ import annotations

from urllib.parse import urlencode

from lxml import html

from lightsear._xpath import eval_xpath_getindex, eval_xpath_list, extract_text
from lightsear.exceptions import CaptchaError, LightsearError
from lightsear.models import SearchResult

RESULTS_PER_PAGE = 10


def _parse_baidu_html(text: str | bytes) -> list[SearchResult]:
    dom = html.fromstring(text)
    out: list[SearchResult] = []

    # Each organic result is a div with both "result" and "c-container" classes.
    for result in eval_xpath_list(
        dom, '//div[contains(@class,"result") and contains(@class,"c-container")]'
    ):
        title_a = eval_xpath_getindex(result, ".//h3//a", 0, None)
        if title_a is None:
            continue

        title = extract_text(title_a) or ""
        href = title_a.get("href", "")
        if not href.startswith("http") or not title:
            continue

        snippet = eval_xpath_getindex(
            result, './/div[contains(@class,"c-abstract")]', 0, None
        )
        content = extract_text(snippet) if snippet is not None else ""

        out.append(SearchResult(title=title, content=content or "", url=href, sources="baidu"))

    return out


def search_baidu(client, keyword: str) -> list[SearchResult]:
    url = f"https://www.baidu.com/s?{urlencode({'wd': keyword, 'rn': RESULTS_PER_PAGE})}"
    resp = client.fetch(
        url,
        network_idle=True,
        # Wait for the main results column to be present
        wait_selector="#content_left",
        wait_selector_state="attached",
    )
    if "wappass.baidu.com" in resp.url and "captcha" in resp.url:
        raise CaptchaError("Baidu captcha page")
    if resp.status >= 400:
        raise LightsearError(f"Baidu returned HTTP {resp.status}")
    return _parse_baidu_html(resp.body)
