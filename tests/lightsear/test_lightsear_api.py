import pytest

import lightsear

PROXY = "http://127.0.0.1:10808"
TIMEOUT_S = 45.0  # seconds (converted to ms internally)


def test_search_unknown_source_raises():
    with pytest.raises(ValueError):
        lightsear.search("query", sources=["not_an_engine"])


def test_search():
    results = lightsear.search("trump tower", timeout=TIMEOUT_S, proxy=PROXY)
    for r in results:
        print(r)
        assert r.sources.strip()
        assert r.title.strip()
        assert r.url.startswith("http")


if __name__ == "__main__":
    test_search()