from __future__ import annotations

import atexit
import logging
import subprocess
import sys
import time
import typing as t
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from lightsear.engines.baidu import search_baidu
from lightsear.engines.bing import search_bing
from lightsear.engines.duckduckgo import search_duckduckgo
from lightsear.engines.google import search_google
from lightsear.exceptions import LightsearError
from lightsear.models import SearchResult
from lightsear.pool import SessionPool  # re-exported for callers who build custom pools

if t.TYPE_CHECKING:
    from collections.abc import Sequence

EngineName = t.Literal["google", "baidu", "bing", "duckduckgo"]

logger = logging.getLogger(__name__)

ENGINES: dict[str, t.Callable[[t.Any, str], list[SearchResult]]] = {
    "google": search_google,
    "baidu": search_baidu,
    "bing": search_bing,
    "duckduckgo": search_duckduckgo,
}

_pool: SessionPool | None = None
_chromium_proc: subprocess.Popen | None = None

# Suppress the console window on Windows when spawning Chromium.
_POPEN_FLAGS: dict[str, t.Any] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------


def initialize_pool(
    *,
    chrome_executable_path: str,
    user_data_dir: str,
    cdp_port: int = 9222,
    cdp_host: str = "localhost",
    headless: bool = True,
    proxy: str | None = None,
    timeout: float = 20.0,
    pool_size: int | None = None,
    startup_timeout: float = 15.0,
) -> None:
    """Launch Chromium and create (or re-create) the global session pool.

    Must be called before :func:`search` or :func:`web_fetch`. Can be called
    again at any time to change settings; the previous Chromium process and
    pool are shut down first.

    :param chrome_executable_path: Path to the Chrome/Chromium executable.
    :param user_data_dir: User-data directory for the browser profile.
    :param cdp_port: Remote-debugging port Chromium will listen on (default ``9222``).
    :param cdp_host: Address Chromium binds the debug port to (default ``"localhost"``).
    :param headless: Run Chromium in headless mode (default ``True``).
    :param proxy: Optional proxy URL passed to Chromium, e.g. ``"http://127.0.0.1:10808"``.
    :param timeout: Per-request browser timeout in seconds (default ``20.0``).
    :param pool_size: Number of concurrent sessions (default: number of engines).
    :param startup_timeout: Seconds to wait for Chromium to become ready (default ``15.0``).
    """
    global _pool, _chromium_proc

    # Shut down any previously managed Chromium process.
    _stop_chromium()

    cmd = [
        chrome_executable_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
    ]
    if headless:
        cmd.append("--headless=new")
    if proxy:
        cmd.append(f"--proxy-server={proxy}")
    # Allow remote connections from non-localhost when cdp_host is overridden.
    if cdp_host not in ("localhost", "127.0.0.1"):
        cmd.append(f"--remote-debugging-address={cdp_host}")

    logger.debug("Launching Chromium: %s", " ".join(cmd))
    _chromium_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_POPEN_FLAGS,
    )

    _wait_for_cdp(cdp_host, cdp_port, startup_timeout, _chromium_proc)

    old = _pool
    _pool = SessionPool(
        size=pool_size or len(ENGINES),
        timeout=timeout,
        cdp_port=cdp_port,
        cdp_host=cdp_host,
    )
    if old is not None:
        old.close()


def _wait_for_cdp(host: str, port: int, startup_timeout: float, proc: subprocess.Popen) -> None:
    """Poll the CDP /json/version endpoint until Chromium is ready."""
    endpoint = f"http://{host}:{port}/json/version"
    deadline = time.monotonic() + startup_timeout
    while True:
        # Check the process hasn't crashed.
        if proc.poll() is not None:
            raise RuntimeError(
                f"Chromium process exited unexpectedly (code {proc.returncode}) "
                f"before the CDP endpoint became available at {endpoint}"
            )
        try:
            urllib.request.urlopen(endpoint, timeout=1)
            logger.debug("Chromium CDP ready at %s", endpoint)
            return
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() >= deadline:
            proc.kill()
            raise RuntimeError(
                f"Chromium did not become ready at {endpoint} within {startup_timeout}s"
            )
        time.sleep(0.25)


def _stop_chromium() -> None:
    global _chromium_proc
    proc = _chromium_proc
    if proc is None:
        return
    _chromium_proc = None
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _get_pool() -> SessionPool:
    if _pool is None:
        raise RuntimeError(
            "lightsear pool is not initialized; call lightsear.initialize_pool() first"
        )
    return _pool


def _close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
    _stop_chromium()


atexit.register(_close_pool)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_sources(sources: "Sequence[str] | None") -> list[str]:
    names = list(sources) if sources is not None else list(ENGINES.keys())
    for name in names:
        if name not in ENGINES:
            raise ValueError(f"Unknown source {name!r}; valid: {sorted(ENGINES)}")
    return names


def _run_engine(pool: SessionPool, name: str, keyword: str) -> list[SearchResult]:
    return pool.submit(ENGINES[name], keyword).result()


def _run_web_fetch(session: t.Any, url: str, wait: int) -> t.Any:
    return session.fetch(url, wait=wait)


def _strip_scripts_and_styles(html_text: str) -> str:
    from lxml import html

    root = html.fromstring(html_text)
    for node in root.xpath("//script|//style"):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return html.tostring(root, encoding="unicode", method="html")


def _execute_search(
    pool: SessionPool,
    keyword: str,
    names: "Sequence[str]",
    *,
    parallel: bool,
) -> tuple[list[SearchResult], dict[str, Exception]]:
    results_by_engine: dict[str, list[SearchResult]] = {}
    errors: dict[str, Exception] = {}

    if parallel and len(names) > 1:
        with ThreadPoolExecutor(max_workers=min(len(names), pool.size)) as executor:
            futures = {name: executor.submit(_run_engine, pool, name, keyword) for name in names}
            for name in names:
                try:
                    results_by_engine[name] = futures[name].result()
                except Exception as exc:
                    errors[name] = exc
                    logger.warning("Engine %r failed: %s", name, exc)
    else:
        for name in names:
            try:
                results_by_engine[name] = _run_engine(pool, name, keyword)
            except Exception as exc:
                errors[name] = exc
                logger.warning("Engine %r failed: %s", name, exc)

    raw: list[SearchResult] = []
    for name in names:
        raw.extend(results_by_engine.get(name, []))
    return raw, errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search(
    keyword: str,
    *,
    sources: "Sequence[str] | None" = None,
    parallel: bool = True,
) -> list[SearchResult]:
    """Run web search on one or more engines and return deduplicated, sorted results.

    Results are aggregated by URL across all queried engines. URLs returned by
    multiple engines appear first (sorted by hit count descending). Each
    :class:`SearchResult` exposes ``sources`` — a comma-separated string of
    every engine that returned that URL, e.g. ``'google,bing'``.

    Call :func:`initialize_pool` once before the first use to set up the
    browser session pool.

    :param keyword: Query string.
    :param sources: Engine names to query; default is all built-in engines.
    :param parallel: Run engine searches concurrently when more than one source is used.
    """
    names = _validate_sources(sources)
    raw, errors = _execute_search(_get_pool(), keyword, names, parallel=parallel)

    if errors and len(errors) == len(names):
        messages = "; ".join(f"{n}: {e}" for n, e in errors.items())
        raise LightsearError(f"All engines failed — {messages}")

    # Aggregate by URL: merge sources and keep the first-seen title/content.
    seen: dict[str, list[str]] = defaultdict(list)
    first: dict[str, SearchResult] = {}
    for result in raw:
        url = result.url
        if url not in first:
            first[url] = result
        seen[url].append(result.sources)

    # Sort by hit count (descending), then by original appearance order.
    order = {url: i for i, url in enumerate(first)}
    aggregated: list[SearchResult] = []
    for url, engine_hits in sorted(
        seen.items(),
        key=lambda kv: (-len(kv[1]), order[kv[0]]),
    ):
        base = first[url]
        merged_sources = ",".join(dict.fromkeys(engine_hits))
        aggregated.append(
            SearchResult(
                title=base.title,
                content=base.content,
                url=url,
                sources=merged_sources,
            )
        )
    return aggregated


def web_fetch(
    url: str,
    *,
    mode: t.Literal["markdown", "text"] = "markdown",
    wait: int = 8_000,
) -> str:
    """Fetch a URL and extract readable content as markdown or plain text.

    Call :func:`initialize_pool` once before the first use to set up the
    browser session pool.

    :param url: URL to fetch.
    :param mode: ``'markdown'`` (default) or ``'text'``.
    :param wait: Extra milliseconds to wait for JS rendering before scraping.
    """
    if mode not in {"markdown", "text"}:
        raise ValueError("mode must be 'markdown' or 'text'")

    # Keep this import local so environments without markdown dependencies
    # can still import and use search-only APIs.
    from markdownify import markdownify as to_markdown

    response = _get_pool().submit(_run_web_fetch, url, wait).result()
    html_text = response.body.decode("utf-8", errors="replace")
    cleaned_html = _strip_scripts_and_styles(html_text)
    if mode == "markdown":
        return to_markdown(cleaned_html)
    return cleaned_html


__all__ = [
    "initialize_pool",
    "search",
    "web_fetch",
    "SessionPool",
    "SearchResult",
    "ENGINES",
    "search_google",
    "search_baidu",
    "search_bing",
    "search_duckduckgo",
]
