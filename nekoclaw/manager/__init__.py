"""Runtime management helpers exposed to channel APIs.

This package provides user-facing management operations (editing provider
configuration, installing/enabling/disabling skills, etc.) that need to be
callable at runtime from the NekoChat web UI.
"""

from nekoclaw.manager.runtime import (
    get_config,
    get_provider,
    set_runtime,
)

__all__ = [
    "get_config",
    "get_provider",
    "set_runtime",
]
