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


def test_search_decodes_chinese_url_characters(monkeypatch: pytest.MonkeyPatch):
    raw_url = "https://example.com/%E4%BD%A0%E5%A5%BD%20world"
    monkeypatch.setattr(lightsear, "_pool", object())
    monkeypatch.setattr(lightsear, "_ensure_chromium_alive", lambda: None)
    monkeypatch.setattr(
        lightsear,
        "_execute_search",
        lambda *_args, **_kwargs: (
            [
                lightsear.SearchResult(
                    title="title",
                    content="content",
                    url=raw_url,
                    sources="google",
                )
            ],
            {},
        ),
    )

    results = lightsear.search("query", sources=["google"])

    assert results[0].url == "https://example.com/你好%20world"


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
