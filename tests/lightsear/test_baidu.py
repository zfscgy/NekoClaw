from scrapling.fetchers import StealthySession

from lightsear.engines import baidu as baidu_engine

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000


def test_search_baidu():
    with StealthySession(timeout=TIMEOUT_MS, proxy=PROXY, headless=True, disable_resources=True) as session:
        results = baidu_engine.search_baidu(session, "百度百科")
    assert len(results) >= 1
    for r in results[:8]:
        assert r.source == "baidu"
        assert r.title.strip()
        assert r.url.startswith("http")
