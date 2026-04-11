from __future__ import annotations

import concurrent.futures as cf
import queue
import threading
import typing as t
from threading import Lock

from lightsear.playwright_client import PlaywrightCDPSession

_SessionCallable = t.Callable[..., t.Any]
_ReleaseCallback = t.Callable[[], None] | None
_Task = tuple[
    _SessionCallable | None,
    tuple[t.Any, ...],
    dict[str, t.Any],
    cf.Future[t.Any],
    _ReleaseCallback,
]


class SessionPool:
    """Pool of browser sessions, each running in its own dedicated thread."""

    def __init__(
        self,
        *,
        size: int = 4,
        timeout: float = 20.0,
        cdp_port: int = 9222,
        cdp_host: str = "localhost",
        session_factory: "t.Callable[[], t.Any] | None" = None,
    ) -> None:
        if size < 1:
            raise ValueError("Session pool size must be at least 1")

        self.size = size
        self.timeout = timeout
        self.cdp_port = cdp_port
        self.cdp_host = cdp_host
        self._session_factory = session_factory
        self._closed = False
        self._state_lock = Lock()

        self._slots: list[tuple[queue.Queue[_Task], threading.Thread]] = []
        self._available: queue.Queue[queue.Queue[_Task]] = queue.Queue(maxsize=size)

        for _ in range(size):
            slot_queue: queue.Queue[_Task] = queue.Queue()
            thread = threading.Thread(target=self._run_slot, args=(slot_queue,), daemon=True)
            thread.start()
            self._slots.append((slot_queue, thread))
            self._available.put(slot_queue)

    def _open_session(self) -> tuple[t.Any, t.Any]:
        managed = (
            self._session_factory()
            if self._session_factory is not None
            else PlaywrightCDPSession(
                cdp_port=self.cdp_port,
                cdp_host=self.cdp_host,
                timeout=int(self.timeout * 1000),
            )
        )
        session = managed.__enter__() if hasattr(managed, "__enter__") else managed
        return managed, session

    def _run_slot(self, slot_queue: queue.Queue[_Task]) -> None:
        managed_session: t.Any = None
        session: t.Any = None
        try:
            while True:
                fn, args, kwargs, future, release = slot_queue.get()
                if fn is None:
                    future.set_result(None)
                    break
                try:
                    if session is None:
                        managed_session, session = self._open_session()
                    future.set_result(fn(session, *args, **kwargs))
                except Exception as exc:
                    future.set_exception(exc)
                finally:
                    if release is not None:
                        release()
        finally:
            if managed_session is not None and hasattr(managed_session, "__exit__"):
                managed_session.__exit__(None, None, None)

    def __enter__(self) -> "SessionPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def submit(
        self,
        fn: _SessionCallable,
        *args: t.Any,
        **kwargs: t.Any,
    ) -> "cf.Future[t.Any]":
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Session pool is closed")

        slot_queue = self._available.get()
        future: cf.Future[t.Any] = cf.Future()

        def release() -> None:
            with self._state_lock:
                if not self._closed:
                    self._available.put(slot_queue)

        slot_queue.put((fn, args, kwargs, future, release))
        return future

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            slots = list(self._slots)
            self._slots.clear()

        for slot_queue, thread in reversed(slots):
            done: cf.Future[None] = cf.Future()
            slot_queue.put((None, (), {}, done, None))
            done.result()
            thread.join()


