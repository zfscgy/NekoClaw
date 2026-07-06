from typing import Literal

import lightsear

from nekoclaw.config.manager import get_global_config


_web_config = get_global_config().tools.web
if _web_config.chrome_executable_path and _web_config.user_data_dir:
    lightsear.initialize_pool(
        chrome_executable_path=_web_config.chrome_executable_path,
        user_data_dir=_web_config.user_data_dir,
        proxy=_web_config.proxy,
        headless=_web_config.headless,
    )


def lightsear_search(text: str, max_results: int = None) -> list[dict[str, str]]:
    """Return up to max_results search results for the given text.

    Each result is a dict with keys: title, url, body, source.
    """
    _web_config = get_global_config().tools.web
    _enabled_engines: list[str] = [
        name for name, enabled in _web_config.search.engines.model_dump().items() if enabled
    ]

    max_results = max_results or _web_config.search.max_results

    
    if not _enabled_engines:
        return []
    results = lightsear.search(text, sources=_enabled_engines)
    return [
        {"title": r.title, "body": r.content, "url": r.url, "source": r.sources}
        for r in results[:max_results]
    ]


def lightsear_fetch(url: str, mode: Literal["markdown", "html"] = "markdown") -> str:
    return lightsear.web_fetch(url, mode=mode)
