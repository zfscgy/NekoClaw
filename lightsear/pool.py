from __future__ import annotations

import concurrent.futures as cf
import queue
import threading
import typing as t
from threading import Lock

from scrapling.fetchers import StealthySession


class _SessionWorker:
    def __init__(
        self,
        *,
        timeout: float,
        proxy: str | None,
        headless: bool,
        disable_resources: bool,
        session_factory: "t.Callable[[], t.Any] | None" = None,
    ) -> None:
        self.timeout = timeout
        self.proxy = proxy
        self.headless = headless
        self.disable_resources = disable_resources
        self._session_factory = session_factory
        self._tasks: "queue.Queue[tuple[t.Callable[..., t.Any] | None, tuple[t.Any, ...], dict[str, t.Any], cf.Future[t.Any], t.Callable[[], None] | None]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _open_session(self) -> tuple[t.Any, t.Any]:
        managed = (
            self._session_factory()
            if self._session_factory is not None
            else StealthySession(
                timeout=int(self.timeout * 1000),
                proxy=self.proxy,
                headless=self.headless,
                disable_resources=self.disable_resources,
            )
        )
        session = managed.__enter__() if hasattr(managed, "__enter__") else managed
        return managed, session

    def _run(self) -> None:
        managed: t.Any = None
        session: t.Any = None
        try:
            while True:
                fn, args, kwargs, future, release = self._tasks.get()
                if fn is None:
                    future.set_result(None)
                    break
                if session is None:
                    managed, session = self._open_session()
                try:
                    future.set_result(fn(session, *args, **kwargs))
                except Exception as exc:
                    future.set_exception(exc)
                finally:
                    if release is not None:
                        release()
        finally:
            if managed is not None and hasattr(managed, "__exit__"):
                managed.__exit__(None, None, None)

    def submit(
        self,
        fn: t.Callable[..., t.Any],
        *args: t.Any,
        future: "cf.Future[t.Any]",
        release: "t.Callable[[], None] | None" = None,
        **kwargs: t.Any,
    ) -> None:
        self._tasks.put((fn, args, kwargs, future, release))

    def close(self) -> None:
        future: cf.Future[None] = cf.Future()
        self._tasks.put((None, (), {}, future, None))
        future.result()
        self._thread.join()


class SessionPool:
    """Pool of persistent browser sessions for parallel engine execution."""

    def __init__(
        self,
        *,
        size: int = 4,
        timeout: float = 20.0,
        proxy: str | None = None,
        headless: bool = True,
        disable_resources: bool = True,
        session_factory: "t.Callable[[], t.Any] | None" = None,
    ) -> None:
        if size < 1:
            raise ValueError("Session pool size must be at least 1")

        self.size = size
        self.timeout = timeout
        self.proxy = proxy
        self.headless = headless
        self.disable_resources = disable_resources
        self._session_factory = session_factory
        self._available: queue.Queue[_SessionWorker] = queue.Queue(maxsize=size)
        self._workers: list[_SessionWorker] = []
        self._closed = False
        self._state_lock = Lock()

        for _ in range(size):
            worker = _SessionWorker(
                timeout=timeout,
                proxy=proxy,
                headless=headless,
                disable_resources=disable_resources,
                session_factory=session_factory,
            )
            self._workers.append(worker)
            self._available.put(worker)

    def __enter__(self) -> "SessionPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def submit(
        self,
        fn: t.Callable[..., t.Any],
        *args: t.Any,
        **kwargs: t.Any,
    ) -> "cf.Future[t.Any]":
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Session pool is closed")

        worker = self._available.get()
        future: cf.Future[t.Any] = cf.Future()

        def release() -> None:
            with self._state_lock:
                if not self._closed:
                    self._available.put(worker)

        worker.submit(fn, *args, future=future, release=release, **kwargs)
        return future

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            workers = list(self._workers)
            self._workers.clear()

        for worker in reversed(workers):
            worker.close()


class SessionPoolManager:
    """Cache persistent session pools by their runtime configuration."""

    def __init__(
        self,
        pool_factory: "t.Callable[..., SessionPool] | None" = None,
    ) -> None:
        self._pools: dict[tuple[int, float, str | None, bool, bool], SessionPool] = {}
        self._lock = Lock()
        self._closed = False
        self._pool_factory = pool_factory or SessionPool

    def get_pool(
        self,
        *,
        size: int,
        timeout: float,
        proxy: str | None,
        headless: bool = True,
        disable_resources: bool = True,
    ) -> SessionPool:
        key = (size, timeout, proxy, headless, disable_resources)
        with self._lock:
            if self._closed:
                raise RuntimeError("Session pool manager is closed")
            pool = self._pools.get(key)
            if pool is None:
                pool = self._pool_factory(
                    size=size,
                    timeout=timeout,
                    proxy=proxy,
                    headless=headless,
                    disable_resources=disable_resources,
                )
                self._pools[key] = pool
            return pool

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pools = list(self._pools.values())
            self._pools.clear()

        for pool in reversed(pools):
            pool.close()
