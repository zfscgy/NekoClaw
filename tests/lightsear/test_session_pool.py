import itertools
import threading
import time

import pytest

import lightsear
from lightsear.models import SearchResult
from lightsear.pool import SessionPool


class FakeManagedSession:
    def __init__(self, ident: int, closed_ids: list[int]):
        self.ident = ident
        self._closed_ids = closed_ids

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._closed_ids.append(self.ident)


def make_session_factory(closed_ids: list[int]):
    counter = itertools.count()

    def factory():
        return FakeManagedSession(next(counter), closed_ids)

    return factory


def test_search_reuses_persistent_sessions():
    closed_ids: list[int] = []
    session_ids: list[int] = []

    def fake_engine(session, keyword: str) -> list[SearchResult]:
        session_ids.append(session.ident)
        return [
            SearchResult(
                title=keyword,
                content="",
                url=f"https://example.com/{keyword}",
                sources="google",
            )
        ]

    original_engines = lightsear.ENGINES
    original_pool = lightsear._pool
    lightsear.ENGINES = {"google": fake_engine}
    lightsear._pool = SessionPool(size=1, session_factory=make_session_factory(closed_ids))
    try:
        lightsear.search("first", sources=["google"], parallel=False)
        lightsear.search("second", sources=["google"], parallel=False)
    finally:
        lightsear._pool.close()
        lightsear._pool = original_pool
        lightsear.ENGINES = original_engines

    assert session_ids == [0, 0]
    assert closed_ids == [0]


def test_search_parallel_uses_multiple_pooled_sessions():
    closed_ids: list[int] = []
    seen_session_ids: set[int] = set()
    concurrency = {"current": 0, "max": 0}
    lock = threading.Lock()

    def make_engine(name: str):
        def fake_engine(session, keyword: str) -> list[SearchResult]:
            with lock:
                seen_session_ids.add(session.ident)
                concurrency["current"] += 1
                concurrency["max"] = max(concurrency["max"], concurrency["current"])
            try:
                time.sleep(0.1)
            finally:
                with lock:
                    concurrency["current"] -= 1
            return [
                SearchResult(
                    title=f"{name}:{keyword}",
                    content="",
                    url=f"https://example.com/{name}",
                    sources=name,
                )
            ]

        return fake_engine

    original_engines = lightsear.ENGINES
    original_pool = lightsear._pool
    lightsear.ENGINES = {
        "google": make_engine("google"),
        "bing": make_engine("bing"),
    }
    lightsear._pool = SessionPool(size=2, session_factory=make_session_factory(closed_ids))
    try:
        results = lightsear.search("query", sources=["google", "bing"], parallel=True)
    finally:
        lightsear._pool.close()
        lightsear._pool = original_pool
        lightsear.ENGINES = original_engines

    assert concurrency["max"] == 2
    assert seen_session_ids == {0, 1}
    assert [result.sources for result in results] == ["google", "bing"]
    assert closed_ids == [1, 0]


def test_initialize_pool_replaces_existing_pool(monkeypatch: pytest.MonkeyPatch):
    original_pool = lightsear._pool
    monkeypatch.setattr(lightsear, "_pool", None)
    try:
        lightsear.initialize_pool(chrome_executable_path=r"C:\chrome.exe", user_data_dir=r"C:\tmp\profile")
        first_pool = lightsear._pool

        lightsear.initialize_pool(chrome_executable_path=r"C:\chrome.exe", user_data_dir=r"C:\tmp\profile")
        second_pool = lightsear._pool

        assert first_pool is not second_pool
        assert first_pool._closed
    finally:
        if lightsear._pool is not None:
            lightsear._pool.close()
        lightsear._pool = original_pool


def test_search_uses_global_pool(monkeypatch):
    closed_ids: list[int] = []
    session_ids: list[int] = []

    def fake_engine(session, keyword: str) -> list[SearchResult]:
        session_ids.append(session.ident)
        return [
            SearchResult(
                title=keyword,
                content="",
                url=f"https://example.com/{keyword}",
                sources="google",
            )
        ]

    fake_pool = SessionPool(size=1, session_factory=make_session_factory(closed_ids))
    original_engines = lightsear.ENGINES
    original_pool = lightsear._pool
    lightsear.ENGINES = {"google": fake_engine}
    lightsear._pool = fake_pool
    try:
        lightsear.search("first", sources=["google"], parallel=False)
        lightsear.search("second", sources=["google"], parallel=False)
    finally:
        lightsear.ENGINES = original_engines
        lightsear._pool = original_pool
        fake_pool.close()

    assert session_ids == [0, 0]
    assert closed_ids == [0]
