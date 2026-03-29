from lightsear.engines import baidu as baidu_engine
from lightsear.playwright_client import PlaywrightCDPSession

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000
REMOTE_DEBUG_PORT = 9222


def test_search_baidu():
    with PlaywrightCDPSession(
        remote_debug_port=REMOTE_DEBUG_PORT,
        timeout=TIMEOUT_MS,
        proxy=PROXY,
        headless=True,
    ) as session:
        results = baidu_engine.search_baidu(session, "百度百科")
    assert len(results) >= 1
    for r in results[:8]:
        assert r.sources == "baidu"
        assert r.title.strip()
        assert r.url.startswith("http")
