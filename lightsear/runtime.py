"""Chromium process + shared session-pool lifecycle.

The public API in :mod:`lightsear.searcher` and :mod:`lightsear.fetcher` operates
on a single, lazily-initialized :class:`SessionPool` that is backed by a
locally-managed Chromium process listening on a CDP port. This module owns
that process and the pool that connects to it.

Tests can replace the module-level ``_pool`` and ``_launch_config`` attributes
to drive the search/fetch helpers without spawning a real browser.
"""

from __future__ import annotations

import atexit
import logging
import subprocess
import sys
import threading
import time
import typing as t
import urllib.error
import urllib.request

from lightsear.engines import ENGINES
from lightsear.pool import SessionPool

logger = logging.getLogger(__name__)

# Suppress the console window on Windows when spawning Chromium.
_POPEN_FLAGS: dict[str, t.Any] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

_pool: SessionPool | None = None
_chromium_proc: subprocess.Popen | None = None
_launch_config: dict[str, t.Any] | None = None
_restart_lock = threading.Lock()


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

    Must be called before :func:`lightsear.search` or :func:`lightsear.web_fetch`.
    Can be called again at any time to change settings; the previous Chromium
    process and pool are shut down first.

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
    global _pool, _chromium_proc, _launch_config

    _launch_config = {
        "chrome_executable_path": chrome_executable_path,
        "user_data_dir": user_data_dir,
        "cdp_port": cdp_port,
        "cdp_host": cdp_host,
        "headless": headless,
        "proxy": proxy,
        "timeout": timeout,
        "pool_size": pool_size,
        "startup_timeout": startup_timeout,
    }

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


def get_pool() -> SessionPool:
    """Return the active session pool or raise if uninitialized."""
    if _pool is None:
        raise RuntimeError(
            "lightsear pool is not initialized; call lightsear.initialize_pool() first"
        )
    return _pool


def ensure_chromium_alive() -> None:
    """Restart Chromium and the session pool if the CDP port is unreachable.

    Intended to recover when the user closes the browser window while the
    process is still running; callers invoke this before dispatching work
    that requires a live browser.
    """
    config = _launch_config
    if config is None:
        return
    if _is_cdp_alive(config["cdp_host"], config["cdp_port"]):
        return
    with _restart_lock:
        # Re-check under the lock so concurrent callers restart only once.
        if _is_cdp_alive(config["cdp_host"], config["cdp_port"]):
            return
        logger.warning(
            "CDP endpoint %s:%s is unreachable; restarting Chromium",
            config["cdp_host"],
            config["cdp_port"],
        )
        initialize_pool(**config)


def _is_cdp_alive(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """Return True if the CDP /json/version endpoint responds."""
    endpoint = f"http://{host}:{port}/json/version"
    try:
        urllib.request.urlopen(endpoint, timeout=timeout)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_for_cdp(host: str, port: int, startup_timeout: float, proc: subprocess.Popen) -> None:
    """Poll the CDP /json/version endpoint until Chromium is ready."""
    endpoint = f"http://{host}:{port}/json/version"
    deadline = time.monotonic() + startup_timeout
    while True:
        if proc.poll() is not None:
            raise RuntimeError(
                f"Chromium process exited unexpectedly (code {proc.returncode}) "
                f"before the CDP endpoint became available at {endpoint}"
            )
        if _is_cdp_alive(host, port):
            logger.debug("Chromium CDP ready at %s", endpoint)
            return
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


def _close_pool() -> None:
    global _pool, _launch_config
    if _pool is not None:
        _pool.close()
        _pool = None
    _stop_chromium()
    _launch_config = None


atexit.register(_close_pool)


__all__ = [
    "ensure_chromium_alive",
    "get_pool",
    "initialize_pool",
]
