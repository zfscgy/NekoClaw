"""Per-engine search functions (SearXNG-derived parsers)."""

from lightsear.engines.baidu import search_baidu
from lightsear.engines.bing import search_bing
from lightsear.engines.duckduckgo import search_duckduckgo
from lightsear.engines.google import search_google

__all__ = [
    "search_baidu",
    "search_bing",
    "search_duckduckgo",
    "search_google",
]
