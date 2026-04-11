from typing import Literal

import lightsear

from nekoclaw.config import load_config

_web_config = load_config().tools.web

if _web_config.chrome_executable_path and _web_config.user_data_dir:
    lightsear.initialize_pool(
        chrome_executable_path=_web_config.chrome_executable_path,
        user_data_dir=_web_config.user_data_dir,
        proxy=_web_config.proxy,
        headless=_web_config.headless,
    )


def lightsear_search(text: str, max_results: int = _web_config.search.max_results) -> list[dict[str, str]]:
    """Return up to max_results search results for the given text.

    Each result is a dict with keys: title, url, body, source.
    """
    results = lightsear.search(text)
    return [
        {"title": r.title, "body": r.content, "url": r.url, "source": r.sources}
        for r in results[:max_results]
    ]


def lightsear_fetch(url: str, mode: Literal["markdown", "text"] = "markdown") -> str:
    return lightsear.web_fetch(url, mode=mode)
