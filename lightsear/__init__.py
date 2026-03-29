from __future__ import annotations

import atexit
import logging
import typing as t
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from lightsear.engines.baidu import search_baidu
from lightsear.engines.bing import search_bing
from lightsear.engines.duckduckgo import search_duckduckgo
from lightsear.engines.google import search_google
from lightsear.exceptions import LightsearError
from lightsear.models import SearchResult
from lightsear.pool import SessionPool, SessionPoolManager

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

DEFAULT_TIMEOUT = 20.0
DEFAULT_POOL_SIZE = len(ENGINES)
_SESSION_POOL_MANAGER = SessionPoolManager()
atexit.register(_SESSION_POOL_MANAGER.close)


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


def _get_persistent_pool(
    *,
    size: int,
    timeout: float,
    remote_debug_port: int,
    proxy: str | None,
    headless: bool = True,
) -> SessionPool:
    return _SESSION_POOL_MANAGER.get_pool(
        size=size,
        timeout=timeout,
        remote_debug_port=remote_debug_port,
        proxy=proxy,
        headless=headless,
    )


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
            futures = {
                name: executor.submit(_run_engine, pool, name, keyword) for name in names
            }
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


def search(
    keyword: str,
    *,
    sources: "Sequence[str] | None" = None,
    timeout: float = DEFAULT_TIMEOUT,
    remote_debug_port: int = 9222,
    proxy: "str | None" = None,
    session_pool: "SessionPool | None" = None,
    pool_size: int | None = None,
    parallel: bool = True,
) -> list[SearchResult]:
    """Run web search on one or more engines and return deduplicated, sorted results.

    Results are aggregated by URL across all queried engines. URLs returned by
    multiple engines appear first (sorted by hit count descending). Each
    :class:`SearchResult` exposes ``sources`` — a comma-separated string of
    every engine that returned that URL, e.g. ``'google,bing'``.

    :param keyword: Query string.
    :param sources: Engine names to query; default is all built-in engines.
    :param timeout: Timeout in seconds (converted to ms for the browser session).
    :param remote_debug_port: Browser remote debugging port used by Playwright (e.g. ``9222``).
    :param proxy: Optional proxy URL, e.g. ``"http://127.0.0.1:10808"``.
    :param session_pool: Optional persistent session pool to reuse across calls.
    :param pool_size: Number of sessions in the cached default pool for this config.
    :param parallel: Run engine searches concurrently when more than one source is used.
    """
    names = _validate_sources(sources)
    if pool_size is not None and pool_size < 1:
        raise ValueError("pool_size must be at least 1")

    if session_pool is None:
        default_pool_size = len(names) if parallel and names else 1
        requested_pool_size = pool_size or default_pool_size
        pool = _get_persistent_pool(
            size=requested_pool_size,
            timeout=timeout,
            remote_debug_port=remote_debug_port,
            proxy=proxy,
        )
        raw, errors = _execute_search(pool, keyword, names, parallel=parallel)
    else:
        raw, errors = _execute_search(session_pool, keyword, names, parallel=parallel)

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
        merged_sources = ",".join(dict.fromkeys(engine_hits))  # deduplicated, ordered
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
    timeout: float = 30.0,
    remote_debug_port: int = 9222,
    proxy: str | None = None,
    headless: bool = True,
    wait: int = 8_000,
    session_pool: "SessionPool | None" = None,
    pool_size: int | None = None,
) -> str:
    """Fetch a URL and extract readable content as markdown or text.

    This mirrors nanobot's previous ``web_fetch`` behavior, but is exposed as a
    first-class lightsear API for reuse across projects.
    """
    if mode not in {"markdown", "text"}:
        raise ValueError("mode must be 'markdown' or 'text'")
    if pool_size is not None and pool_size < 1:
        raise ValueError("pool_size must be at least 1")

    # Keep this import local so users can still import search-only APIs in
    # environments without markdown conversion dependencies.
    from markdownify import markdownify as to_markdown

    if session_pool is None:
        requested_pool_size = pool_size or 1
        pool = _get_persistent_pool(
            size=requested_pool_size,
            timeout=timeout,
            remote_debug_port=remote_debug_port,
            proxy=proxy,
            headless=headless,
        )
    else:
        pool = session_pool

    response = pool.submit(_run_web_fetch, url, wait).result()
    html_text = response.body.decode("utf-8", errors="replace")
    cleaned_html = _strip_scripts_and_styles(html_text)
    if mode == "markdown":
        return to_markdown(cleaned_html)
    return cleaned_html


__all__ = [
    "search",
    "web_fetch",
    "SessionPool",
    "SessionPoolManager",
    "SearchResult",
    "ENGINES",
    "search_google",
    "search_baidu",
    "search_bing",
    "search_duckduckgo",
]
