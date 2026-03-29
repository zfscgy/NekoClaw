from lightsear.engines import google as google_engine
from lightsear.playwright_client import PlaywrightCDPSession

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000
REMOTE_DEBUG_PORT = 9222


def test_search_google():
    with PlaywrightCDPSession(
        remote_debug_port=REMOTE_DEBUG_PORT,
        timeout=TIMEOUT_MS,
        proxy=PROXY,
        headless=True,
    ) as session:
        results = google_engine.search_google(session, "python programming language")
    assert len(results) >= 1
    for r in results[:8]:
        print(r)
        assert r.sources == "google"
        assert r.title.strip()
        assert r.url.startswith("http")


if __name__ == "__main__":
    test_search_google()