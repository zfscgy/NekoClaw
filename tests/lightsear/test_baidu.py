from lightsear.engines import baidu as baidu_engine
from lightsear.playwright_client import PlaywrightCDPSession

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000
CHROME_EXECUTABLE_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = r"C:\tmp\lightsear-profile-baidu"


def test_search_baidu():
    with PlaywrightCDPSession(
        chrome_executable_path=CHROME_EXECUTABLE_PATH,
        user_data_dir=USER_DATA_DIR,
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
