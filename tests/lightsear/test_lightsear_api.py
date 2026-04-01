import pytest

import lightsear

PROXY = "http://127.0.0.1:10808"
TIMEOUT_S = 45.0
CHROME_EXECUTABLE_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = r"C:\tmp\lightsear-profile"


def test_search_unknown_source_raises():
    with pytest.raises(ValueError):
        lightsear.search("query", sources=["not_an_engine"])


def test_search_requires_pool_init(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lightsear, "_pool", None)
    with pytest.raises(RuntimeError, match="initialize_pool"):
        lightsear.search("query")


def test_search():
    lightsear.initialize_pool(
        chrome_executable_path=CHROME_EXECUTABLE_PATH,
        user_data_dir=USER_DATA_DIR,
        proxy=PROXY,
        timeout=TIMEOUT_S,
        headless=False,
    )

    results = lightsear.search("trump tower")
    for r in results:
        print(r)
        assert r.sources.strip()
        assert r.title.strip()
        assert r.url.startswith("http")


if __name__ == "__main__":
    test_search()
