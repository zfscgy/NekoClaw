"""Logging configuration for the nekoclaw gateway.

The codebase logs through :mod:`loguru` (``from loguru import logger``).
Loguru ships with a default ``stderr`` sink at ``DEBUG`` level, which means
every ``logger.info(...)`` call across the agent loop, channels, and session
manager prints during startup and drowns out the curated Rich messages.

This module replaces the default sink with two we control:

- A **console** sink on ``stderr`` that stays quiet by default (``WARNING``+)
  so the cat-girl startup banner is not drowned out.  ``--verbose`` raises it
  to ``DEBUG`` so the full firehose is visible interactively.
- A **file** sink under ``~/.nekoclaw/logs/nekoclaw.log`` that always captures
  ``DEBUG``+ with size-based rotation and 14-day retention.  This means
  routine ``logger.info(...)`` chatter is never lost — it is just moved
  off-screen.

Standard-library ``logging`` (used by ``aiohttp`` etc.) is also lowered to
``WARNING`` so its propagated records don't leak through the console.

.. note::

   This module is deliberately **not** named ``logging.py`` — that would
   shadow the stdlib ``logging`` module and cause a circular-import crash
   the moment any imported library (e.g. loguru itself) tries to
   ``import logging`` while our module is still initialising.
"""

from __future__ import annotations

import logging as _stdlib_logging
import sys
from pathlib import Path

from loguru import logger


_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | "
    "<level>{level: <7}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "{name}:{function}:{line} - "
    "{message}"
)


def _resolve_log_path() -> Path:
    """Return the active log file path, creating the directory if needed.

    We import ``get_logs_dir`` lazily so this module stays importable even
    before the config layer has fully loaded.
    """
    from nekoclaw.config.paths import get_logs_dir

    return get_logs_dir() / "nekoclaw.log"


def configure_logging(verbose: bool = False) -> Path:
    """Apply the project-wide logging policy.

    Args:
        verbose: When ``True``, show ``DEBUG``+ on the console; otherwise
            only ``WARNING``+ is shown so the curated startup banner stays
            readable.  The file sink always records at ``DEBUG``.

    Returns:
        The absolute path to the active log file.  Useful so callers can
        surface it in the startup banner.
    """
    console_level = "DEBUG" if verbose else "WARNING"
    stdlib_level = _stdlib_logging.DEBUG if verbose else _stdlib_logging.WARNING

    log_path = _resolve_log_path()

    logger.remove()
    logger.add(
        sys.stderr,
        level=console_level,
        format=_CONSOLE_FORMAT,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        str(log_path),
        level="DEBUG",
        format=_FILE_FORMAT,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )

    _stdlib_logging.basicConfig(level=stdlib_level, force=True)
    for noisy in ("aiohttp.access", "asyncio", "urllib3", "httpx", "httpcore"):
        _stdlib_logging.getLogger(noisy).setLevel(stdlib_level)

    return log_path
