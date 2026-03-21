"""Tests for nanobot.tools.web_search.search_duckduckgo."""

from unittest.mock import MagicMock, patch

import pytest

from nanobot.tools.web_search import search_duckduckgo


def test__search_duckduckgo():
    query = "2026年3月20日新闻"
    results = search_duckduckgo(query, 10)
    assert len(results) == 10
    assert all(isinstance(result, dict) for result in results)
    assert all("title" in result for result in results)
    assert all("href" in result for result in results)
    assert all("body" in result for result in results)
    for result in results:
        print(result["title"])
        print(result["href"])
        print(result["body"])
        print("--------")


if __name__ == "__main__":
    test__search_duckduckgo()
