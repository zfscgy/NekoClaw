from scrapling.fetchers import StealthySession, DynamicSession

from lightsear.engines import duckduckgo as ddg_engine

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000


def test_search_duckduckgo():
    with DynamicSession(timeout=TIMEOUT_MS, proxy=PROXY, headless=True, disable_resources=True) as session:
        results = ddg_engine.search_duckduckgo(session, "open source software")
    assert len(results) >= 1
    for r in results[:8]:
        print(r)
        assert r.source == "duckduckgo"
        assert r.title.strip()
        assert r.url.startswith("http")


if __name__ == "__main__":
    test_search_duckduckgo()