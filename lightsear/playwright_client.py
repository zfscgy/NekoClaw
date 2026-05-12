from __future__ import annotations

import asyncio
import concurrent.futures as cf
from dataclasses import dataclass
import logging
import threading
import typing as t

from playwright.async_api import Browser, BrowserContext, Playwright, TimeoutError as PlaywrightTimeoutError, async_playwright

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchResponse:
    url: str
    status: int
    body: bytes


@dataclass(frozen=True, slots=True)
class _BrowserKey:
    cdp_endpoint: str


@dataclass(slots=True)
class _RuntimeHandle:
    runtime: "_AsyncBrowserRuntime"
    ref_count: int = 0


_RUNTIMES: dict[_BrowserKey, _RuntimeHandle] = {}
_BROWSER_LOCK = threading.Lock()


class _AsyncBrowserRuntime:
    def __init__(self, key: _BrowserKey) -> None:
        self._key = key
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise RuntimeError(
                f"Failed to connect to Chromium at {self._key.cdp_endpoint}"
            ) from self._startup_error

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._startup())
        except BaseException as exc:  # pragma: no cover - startup failure path
            self._startup_error = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(self._shutdown())
            loop.close()

    async def _startup(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            self._key.cdp_endpoint
        )
        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = await self._browser.new_context()

    async def _shutdown(self) -> None:
        self._context = None
        if self._browser is not None:
            # close() only disconnects Playwright; it does not kill the browser process.
            # Tolerate a dead CDP connection (e.g. user closed the browser) so
            # the runtime can still be torn down cleanly.
            try:
                await self._browser.close()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                logger.debug("Ignoring browser.close() error during shutdown: %s", exc)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                logger.debug("Ignoring playwright.stop() error during shutdown: %s", exc)
            self._playwright = None

    def submit(self, coro: "asyncio.coroutines") -> cf.Future[t.Any]:
        if self._loop is None:
            raise RuntimeError("Playwright runtime is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join()
        self._loop = None
        self._thread = None

    async def fetch(
        self,
        *,
        url: str,
        timeout: int,
        wait: int | None,
        wait_selector: str | None,
        wait_selector_state: str,
    ) -> FetchResponse:
        if self._context is None:
            raise RuntimeError("Browser context is not ready")
        page = await self._context.new_page()
        page.set_default_timeout(timeout)
        page.set_default_navigation_timeout(timeout)
        await page.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            if wait_selector:
                await page.wait_for_selector(
                    wait_selector,
                    state=wait_selector_state,
                    timeout=timeout,
                )
            if wait and wait > 0:
                await page.wait_for_timeout(wait)
            html_body = (await page.content()).encode("utf-8")
            status = response.status if response is not None else 200
            return FetchResponse(url=page.url, status=status, body=html_body)
        finally:
            await page.close()


class PlaywrightCDPSession:
    """Browser session adapter that connects to a running Chromium instance
    via the Chrome DevTools Protocol (CDP) debug port.

    Chromium must be launched externally with ``--remote-debugging-port=<port>``
    (and optionally ``--remote-debugging-address=<host>``) before creating a
    session.  A single CDP connection is shared across all sessions targeting
    the same endpoint.

    Example launch command::

        chromium --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-profile
    """

    def __init__(
        self,
        *,
        cdp_port: int = 9222,
        cdp_host: str = "localhost",
        timeout: int,
    ) -> None:
        self.cdp_port = cdp_port
        self.cdp_host = cdp_host
        self.timeout = timeout
        cdp_endpoint = f"http://{cdp_host}:{cdp_port}"
        self._key = _BrowserKey(cdp_endpoint=cdp_endpoint)
        self._runtime: _AsyncBrowserRuntime | None = None

    def __enter__(self) -> "PlaywrightCDPSession":
        with _BROWSER_LOCK:
            handle = _RUNTIMES.get(self._key)
            if handle is None:
                runtime = _AsyncBrowserRuntime(self._key)
                runtime.start()
                handle = _RuntimeHandle(runtime=runtime, ref_count=0)
                _RUNTIMES[self._key] = handle
            handle.ref_count += 1
            self._runtime = handle.runtime
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        runtime_to_stop: _AsyncBrowserRuntime | None = None
        with _BROWSER_LOCK:
            handle = _RUNTIMES.get(self._key)
            if handle is not None:
                handle.ref_count -= 1
                if handle.ref_count <= 0:
                    runtime_to_stop = handle.runtime
                    del _RUNTIMES[self._key]
        if runtime_to_stop is not None:
            runtime_to_stop.stop()
        self._runtime = None

    def fetch(
        self,
        url: str,
        *,
        wait: int | None = None,
        network_idle: bool = False,
        wait_selector: str | None = None,
        wait_selector_state: str = "attached",
        google_search: bool | None = None,  # accepted for compatibility
    ) -> FetchResponse:
        if self._runtime is None:
            raise RuntimeError("Session is not open")
        del google_search
        del network_idle
        try:
            return self._runtime.submit(
                self._runtime.fetch(
                    url=url,
                    timeout=self.timeout,
                    wait=wait,
                    wait_selector=wait_selector,
                    wait_selector_state=wait_selector_state,
                )
            ).result()
        except PlaywrightTimeoutError:
            raise
