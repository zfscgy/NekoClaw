"""Lightweight web search and fetch backed by a shared Chromium pool.

This package keeps a single browser process alive across calls and routes work
through a fixed-size session pool. Typical usage::

    import lightsear

    lightsear.initialize_pool(
        chrome_executable_path=r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        user_data_dir=r"C:\\tmp\\lightsear-profile",
    )

    results = lightsear.search("trump tower")
    page = lightsear.web_fetch("https://example.com")

The implementation is split across:

- :mod:`lightsear.runtime` — Chromium process + session-pool lifecycle
- :mod:`lightsear.searcher` — multi-engine search aggregation
- :mod:`lightsear.fetcher` — render-aware page fetcher
- :mod:`lightsear.engines` — per-engine HTML parsers and the ``ENGINES`` registry
"""

from __future__ import annotations

from lightsear.engines import (
    ENGINES,
    EngineName,
    search_baidu,
    search_bing,
    search_duckduckgo,
    search_google,
)
from lightsear.exceptions import CaptchaError, LightsearError
from lightsear.fetcher import FetchMode, web_fetch
from lightsear.models import SearchResult
from lightsear.pool import SessionPool
from lightsear.runtime import initialize_pool
from lightsear.searcher import search

__all__ = [
    "ENGINES",
    "CaptchaError",
    "EngineName",
    "FetchMode",
    "LightsearError",
    "SearchResult",
    "SessionPool",
    "initialize_pool",
    "search",
    "search_baidu",
    "search_bing",
    "search_duckduckgo",
    "search_google",
    "web_fetch",
]
