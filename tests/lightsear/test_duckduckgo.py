from lightsear.engines import duckduckgo as ddg_engine
from lightsear.playwright_client import PlaywrightCDPSession

PROXY = "http://127.0.0.1:10808"
TIMEOUT_MS = 30_000
CHROME_EXECUTABLE_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = r"C:\tmp\lightsear-profile-ddg"


def test_search_duckduckgo():
    with PlaywrightCDPSession(
        chrome_executable_path=CHROME_EXECUTABLE_PATH,
        user_data_dir=USER_DATA_DIR,
        timeout=TIMEOUT_MS,
        proxy=PROXY,
        headless=True,
    ) as session:
        results = ddg_engine.search_duckduckgo(session, "open source software")
    assert len(results) >= 1
    for r in results[:8]:
        print(r)
        assert r.sources == "duckduckgo"
        assert r.title.strip()
        assert r.url.startswith("http")


if __name__ == "__main__":
    test_search_duckduckgo()