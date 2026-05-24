"""Per-engine search functions and the engine registry.

Each engine module exposes a ``search_<name>(client, keyword)`` callable that
issues one request via the shared browser session and returns a list of
``SearchResult``. The :data:`ENGINES` registry maps the engine name used by
the public API to its callable.
"""

from __future__ import annotations

import typing as t

from lightsear.engines.baidu import search_baidu
from lightsear.engines.bing import search_bing
from lightsear.engines.duckduckgo import search_duckduckgo
from lightsear.engines.google import search_google
from lightsear.models import SearchResult

EngineName = t.Literal["google", "baidu", "bing", "duckduckgo"]
EngineFn = t.Callable[[t.Any, str], list[SearchResult]]

ENGINES: dict[str, EngineFn] = {
    "google": search_google,
    "baidu": search_baidu,
    "bing": search_bing,
    "duckduckgo": search_duckduckgo,
}

__all__ = [
    "ENGINES",
    "EngineFn",
    "EngineName",
    "search_baidu",
    "search_bing",
    "search_duckduckgo",
    "search_google",
]
