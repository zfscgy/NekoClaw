"""Cross-engine search aggregation."""

from __future__ import annotations

import logging
import typing as t
from concurrent.futures import ThreadPoolExecutor

from lightsear import engines as _engines
from lightsear.exceptions import LightsearError
from lightsear.models import SearchResult
from lightsear.pool import SessionPool
from lightsear.runtime import ensure_chromium_alive, get_pool
from lightsear.utils import decode_url_chinese_only

if t.TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def search(
    keyword: str,
    *,
    sources: "Sequence[str] | None" = None,
    parallel: bool = True,
) -> list[SearchResult]:
    """Run web search on one or more engines and return aggregated, sorted results.

    Results are aggregated by URL across all queried engines. URLs returned by
    multiple engines appear first (sorted by hit count descending). Each
    :class:`SearchResult` exposes ``sources`` — a comma-separated string of
    every engine that returned that URL, e.g. ``'google,bing'``.

    Call :func:`lightsear.initialize_pool` once before the first use to set up
    the browser session pool.

    :param keyword: Query string.
    :param sources: Engine names to query; default is all built-in engines.
    :param parallel: Run engine searches concurrently when more than one source is used.
    """
    names = _validate_sources(sources)
    ensure_chromium_alive()
    raw, errors = _execute_search(get_pool(), keyword, names, parallel=parallel)

    if errors and len(errors) == len(names):
        messages = "; ".join(f"{n}: {e}" for n, e in errors.items())
        raise LightsearError(f"All engines failed — {messages}")

    return _aggregate_by_url(raw)


def _validate_sources(sources: "Sequence[str] | None") -> list[str]:
    registry = _engines.ENGINES
    names = list(sources) if sources is not None else list(registry.keys())
    for name in names:
        if name not in registry:
            raise ValueError(f"Unknown source {name!r}; valid: {sorted(registry)}")
    return names


def _run_engine(pool: SessionPool, name: str, keyword: str) -> list[SearchResult]:
    return pool.submit(_engines.ENGINES[name], keyword).result()


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


def _aggregate_by_url(raw: list[SearchResult]) -> list[SearchResult]:
    """Merge duplicate URLs from different engines into a single result row."""
    sources_per_url: dict[str, list[str]] = {}
    first_seen: dict[str, SearchResult] = {}
    for result in raw:
        url = result.url
        if url not in first_seen:
            first_seen[url] = result
        sources_per_url.setdefault(url, []).append(result.sources)

    # Sort by hit count (descending), then by original appearance order.
    order = {url: i for i, url in enumerate(first_seen)}
    aggregated: list[SearchResult] = []
    for url, engine_hits in sorted(
        sources_per_url.items(),
        key=lambda kv: (-len(kv[1]), order[kv[0]]),
    ):
        base = first_seen[url]
        merged_sources = ",".join(dict.fromkeys(engine_hits))
        aggregated.append(
            SearchResult(
                title=base.title,
                content=base.content,
                url=decode_url_chinese_only(url),
                sources=merged_sources,
            )
        )
    return aggregated


__all__ = ["search"]
