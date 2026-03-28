import itertools
import threading
import time

import lightsear
from lightsear.models import SearchResult
from lightsear.pool import SessionPool, SessionPoolManager


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
    lightsear.ENGINES = {"google": fake_engine}
    try:
        with SessionPool(size=1, session_factory=make_session_factory(closed_ids)) as pool:
            lightsear.search(
                "first",
                sources=["google"],
                session_pool=pool,
                parallel=False,
            )
            lightsear.search(
                "second",
                sources=["google"],
                session_pool=pool,
                parallel=False,
            )
    finally:
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
    lightsear.ENGINES = {
        "google": make_engine("google"),
        "bing": make_engine("bing"),
    }
    try:
        with SessionPool(size=2, session_factory=make_session_factory(closed_ids)) as pool:
            results = lightsear.search(
                "query",
                sources=["google", "bing"],
                session_pool=pool,
                parallel=True,
            )
    finally:
        lightsear.ENGINES = original_engines

    assert concurrency["max"] == 2
    assert seen_session_ids == {0, 1}
    assert [result.sources for result in results] == ["google", "bing"]
    assert closed_ids == [1, 0]


def test_session_pool_manager_reuses_cached_pools():
    closed_ids: list[int] = []

    def pool_factory(**kwargs):
        return SessionPool(
            **kwargs,
            session_factory=make_session_factory(closed_ids),
        )

    manager = SessionPoolManager(pool_factory=pool_factory)
    try:
        first = manager.get_pool(size=1, timeout=20.0, proxy=None)
        second = manager.get_pool(size=1, timeout=20.0, proxy=None)
        third = manager.get_pool(size=2, timeout=20.0, proxy=None)
        assert first is second
        assert first is not third
    finally:
        manager.close()

    assert closed_ids == [2, 1, 0]


def test_search_uses_persistent_manager_pool_by_default():
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

    def pool_factory(**kwargs):
        return SessionPool(
            **kwargs,
            session_factory=make_session_factory(closed_ids),
        )

    original_engines = lightsear.ENGINES
    original_manager = lightsear._SESSION_POOL_MANAGER
    lightsear.ENGINES = {"google": fake_engine}
    manager = SessionPoolManager(pool_factory=pool_factory)
    lightsear._SESSION_POOL_MANAGER = manager
    try:
        lightsear.search(
            "first",
            sources=["google"],
            timeout=21.0,
            pool_size=1,
            parallel=False,
        )
        lightsear.search(
            "second",
            sources=["google"],
            timeout=21.0,
            pool_size=1,
            parallel=False,
        )
    finally:
        lightsear.ENGINES = original_engines
        lightsear._SESSION_POOL_MANAGER = original_manager
        manager.close()

    assert session_ids == [0, 0]
    assert closed_ids == [0]
