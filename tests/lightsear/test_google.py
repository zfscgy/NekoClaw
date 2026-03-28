from scrapling.fetchers import StealthySession, DynamicSession

from lightsear.engines import google as google_engine

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000


def test_search_google():
    with StealthySession(timeout=TIMEOUT_MS, proxy=PROXY, headless=True, disable_resources=True) as session:
        results = google_engine.search_google(session, "python programming language")
    assert len(results) >= 1
    for r in results[:8]:
        print(r)
        assert r.source == "google"
        assert r.title.strip()
        assert r.url.startswith("http")


if __name__ == "__main__":
    test_search_google()