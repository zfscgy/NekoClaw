from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One organic web result from a search engine."""

    title: str
    content: str
    url: str
    sources: str
    """Comma-separated engine names that returned this URL, e.g. ``'google,bing'``."""
