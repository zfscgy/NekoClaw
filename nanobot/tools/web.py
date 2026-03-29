from typing import Literal

import lightsear

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
    return lightsear.web_fetch(
        url,
        mode=mode,
        timeout=30.0,
        proxy=web_config.proxy,
        headless=True,
        wait=8_000,
    )
