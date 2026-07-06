import sys
import types

import pytest

import lightsear
from lightsear import fetcher as lightsear_fetcher
from lightsear import runtime as lightsear_runtime
from lightsear._encoding import decode_html_body


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

    monkeypatch.setattr(lightsear_runtime, "_pool", FakePool())
    monkeypatch.setattr(lightsear_runtime, "ensure_chromium_alive", lambda: None)
    monkeypatch.setitem(sys.modules, "markdownify", types.SimpleNamespace(markdownify=fake_markdownify))

    content = lightsear.web_fetch("https://example.com", mode="markdown", wait=1234)

    assert content == "hello world"
    assert calls["submit"] == {"fn": "_run_web_fetch", "args": ("https://example.com", 1234), "kwargs": {}}
    assert calls["fetch"] == {"url": "https://example.com", "wait": 1234}
    assert "<h1>mock page</h1>" in calls["markdownify"]


def test_web_fetch_html_removes_css_and_js(monkeypatch: pytest.MonkeyPatch):
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

    monkeypatch.setattr(lightsear_runtime, "_pool", FakePool())
    monkeypatch.setattr(lightsear_runtime, "ensure_chromium_alive", lambda: None)

    content = lightsear.web_fetch("https://example.com", mode="html")

    assert "<h1>Title</h1>" in content
    assert "<p>Body</p>" in content
    assert "<script" not in content
    assert "<style" not in content


def test_web_fetch_rejects_unknown_mode():
    with pytest.raises(ValueError, match="mode must be 'markdown' or 'html'"):
        lightsear.web_fetch("https://example.com", mode="text")  # type: ignore[arg-type]


def test_decode_html_body_uses_declared_chinese_encoding():
    body = (
        '<html><head><meta charset="gb2312"></head>'
        "<body><h1>中文标题</h1></body></html>"
    ).encode("gb18030")

    assert "中文标题" in decode_html_body(body)


def test_decode_html_body_prefers_valid_utf8_over_stale_meta_charset():
    body = (
        '<html><head><meta charset="gb2312"></head>'
        "<body><h1>中文标题</h1></body></html>"
    ).encode()

    assert "中文标题" in decode_html_body(body)


def test_web_fetch_requires_pool_init(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lightsear_runtime, "_pool", None)
    with pytest.raises(RuntimeError, match="initialize_pool"):
        lightsear.web_fetch("https://example.com")


def test_fetcher_module_exposes_helpers():
    assert hasattr(lightsear_fetcher, "_run_web_fetch")
    assert hasattr(lightsear_fetcher, "_strip_noise_nodes")
