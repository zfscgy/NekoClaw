"""Runtime management helpers exposed to channel APIs.

This package provides user-facing management operations (editing provider
configuration, installing/enabling/disabling skills, etc.) that need to be
callable at runtime from the NekoChat web UI.

Typical usage::

    from nekoclaw.manager import config as mcfg

    mcfg.set("providers.openai.api_key", "sk-…")
    current = mcfg.get("providers.openai")
"""

from nekoclaw.manager import config, sessions
from nekoclaw.manager.runtime import (
    get_config,
    get_provider,
    set_runtime,
)

__all__ = [
    "config",
    "sessions",
    "get_config",
    "get_provider",
    "set_runtime",
]
