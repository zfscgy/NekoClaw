from __future__ import annotations

from dataclasses import dataclass
import logging
import typing as t

from playwright.sync_api import sync_playwright, Playwright, Browser, Page, BrowserContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchResponse:
    url: str
    status: int
    body: bytes


class PlaywrightCDPSession:
    """Minimal browser session adapter that exposes ``fetch(...)``.

    The implementation connects to a running Chromium-compatible browser
    through its remote debugging port (for example ``9222``).
    """

    def __init__(
        self,
        *,
        remote_debug_port: int,
        timeout: int,
        proxy: str | None = None,
        headless: bool = True,  # kept for API compatibility
    ) -> None:
        self.remote_debug_port = remote_debug_port
        self.timeout = timeout
        self.proxy = proxy
        self.headless = headless
        self._playwright: Playwright = None
        self._browser: Browser = None
        self._context: BrowserContext = None
        self._page: Page = None

    def __enter__(self) -> "PlaywrightCDPSession":
        self._playwright = sync_playwright().start()
        cdp_endpoint = f"http://127.0.0.1:{self.remote_debug_port}"
        self._browser = self._playwright.chromium.connect_over_cdp(
            cdp_endpoint,
            is_local=True,
            timeout=self.timeout,
        )

        # CDP-attached browsers usually expose a default context.
        contexts = list(self._browser.contexts)
        if contexts:
            self._context = contexts[0]
            if self.proxy:
                logger.warning(
                    "Ignoring proxy=%r for existing CDP context; configure proxy on the browser process.",
                    self.proxy,
                )
        else:
            context_options: dict[str, t.Any] = {}
            if self.proxy:
                context_options["proxy"] = {"server": self.proxy}
            self._context = self._browser.new_context(**context_options)

        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._context = None

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
        if self._page is None:
            raise RuntimeError("Session is not open")
        del google_search

        wait_until = "networkidle" if network_idle else "domcontentloaded"
        response = self._page.goto(url, wait_until=wait_until, timeout=self.timeout)
        if wait_selector:
            self._page.wait_for_selector(
                wait_selector,
                state=wait_selector_state,
                timeout=self.timeout,
            )
        if wait and wait > 0:
            self._page.wait_for_timeout(wait)
        html_body = self._page.content().encode("utf-8")
        status = response.status if response is not None else 200
        return FetchResponse(url=self._page.url, status=status, body=html_body)
