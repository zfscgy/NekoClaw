from scrapling.fetchers import StealthySession

from lightsear.engines import bing as bing_engine

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000


def test_search_bing():
    with StealthySession(timeout=TIMEOUT_MS, proxy=PROXY, headless=True, disable_resources=True) as session:
        results = bing_engine.search_bing(session, "wikipedia encyclopedia")
    assert len(results) >= 1
    for r in results[:8]:
        assert r.source == "bing"
        assert r.title.strip()
        assert r.url.startswith("http")
        print(r)


if __name__ == "__main__":
    test_search_bing()