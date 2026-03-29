from lightsear.engines import bing as bing_engine
from lightsear.playwright_client import PlaywrightCDPSession

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000
REMOTE_DEBUG_PORT = 9222


def test_search_bing():
    with PlaywrightCDPSession(
        remote_debug_port=REMOTE_DEBUG_PORT,
        timeout=TIMEOUT_MS,
        proxy=PROXY,
        headless=True,
    ) as session:
        results = bing_engine.search_bing(session, "wikipedia encyclopedia")
    assert len(results) >= 1
    for r in results[:8]:
        assert r.sources == "bing"
        assert r.title.strip()
        assert r.url.startswith("http")
        print(r)


if __name__ == "__main__":
    test_search_bing()