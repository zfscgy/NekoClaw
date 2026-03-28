from typing import Literal

import requests

import lightsear
from scrapling.fetchers import StealthySession
from scrapling.core.shell import Convertor
from markdownify import markdownify

from nanobot.config import load_config
web_config = load_config().tools.web


def lightsear_search(text: str, max_results: int = web_config.search.max_results) -> list[dict[str, str]]:
    """Return up to max_results search results for the given text.

    Each result is a dict with keys: title, url, body, source.
    """
    results = lightsear.search(text, proxy=web_config.proxy)
    results_parsed = [{
        "title": r.title,
        "body": r.content,
        "url": r.url,
        "source": r.sources
    } for r in results[:max_results]]

    return results_parsed


def web_fetch(url: str, mode: Literal["markdown", "text"] = "markdown") -> str:
    with StealthySession(
        timeout=int(30 * 1000),
        proxy=web_config.proxy,
        headless=True,
        disable_resources=True,
    ) as session:
        page = session.fetch(url, wait=8_000)
        return "".join(Convertor._extract_content(page, mode))
