"""Tests for nekoclaw.tools.web_search.search_duckduckgo."""

from nekoclaw.tools.web import lightsear_search


def test__search_duckduckgo():
    query = "今日头条 新闻 2026年3月22日 国际 国内"
    results = lightsear_search(query, 10)
    assert len(results) == 10
    assert all(isinstance(result, dict) for result in results)
    assert all("title" in result for result in results)
    assert all("url" in result for result in results)
    assert all("body" in result for result in results)
    assert all("source" in result for result in results)
    for result in results:
        print(result["title"])
        print(result["url"])
        print(result["body"])
        print(result["source"])
        print("--------")


if __name__ == "__main__":
    test__search_duckduckgo()
