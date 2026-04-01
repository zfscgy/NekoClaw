import sys
import types

import pytest

import lightsear


def test_web_fetch_uses_session_pool_and_markdownify(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    class FakeFuture:
        def __init__(self, value):
            self._value = value

        def result(self):
            return self._value

    class FakeSession:
        def fetch(self, url: str, *, wait: int):
            calls["fetch"] = {"url": url, "wait": wait}
            return types.SimpleNamespace(body=b"<html><body><h1>mock page</h1></body></html>")

    class FakePool:
        def submit(self, fn, *args, **kwargs):
            calls["submit"] = {"fn": fn.__name__, "args": args, "kwargs": kwargs}
            value = fn(FakeSession(), *args, **kwargs)
            return FakeFuture(value)

    def fake_markdownify(page: str):
        calls["markdownify"] = page
        return "hello world"

    monkeypatch.setattr(lightsear, "_pool", FakePool())
    monkeypatch.setitem(sys.modules, "markdownify", types.SimpleNamespace(markdownify=fake_markdownify))

    content = lightsear.web_fetch("https://example.com", mode="markdown", wait=1234)

    assert content == "hello world"
    assert calls["submit"] == {"fn": "_run_web_fetch", "args": ("https://example.com", 1234), "kwargs": {}}
    assert calls["fetch"] == {"url": "https://example.com", "wait": 1234}
    assert "<h1>mock page</h1>" in calls["markdownify"]


def test_web_fetch_text_removes_css_and_js(monkeypatch: pytest.MonkeyPatch):
    class FakeFuture:
        def __init__(self, value):
            self._value = value

        def result(self):
            return self._value

    class FakeSession:
        def fetch(self, url: str, *, wait: int):
            del url, wait
            return types.SimpleNamespace(
                body=(
                    b"<html><head><style>body{color:red}</style><script>alert(1)</script></head>"
                    b"<body><h1>Title</h1><p>Body</p></body></html>"
                )
            )

    class FakePool:
        def submit(self, fn, *args, **kwargs):
            value = fn(FakeSession(), *args, **kwargs)
            return FakeFuture(value)

    monkeypatch.setattr(lightsear, "_pool", FakePool())

    content = lightsear.web_fetch("https://example.com", mode="text")

    assert "<h1>Title</h1>" in content
    assert "<p>Body</p>" in content
    assert "<script" not in content
    assert "<style" not in content


def test_web_fetch_rejects_unknown_mode():
    with pytest.raises(ValueError, match="mode must be 'markdown' or 'text'"):
        lightsear.web_fetch("https://example.com", mode="html")  # type: ignore[arg-type]


def test_web_fetch_requires_pool_init(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lightsear, "_pool", None)
    with pytest.raises(RuntimeError, match="initialize_pool"):
        lightsear.web_fetch("https://example.com")
