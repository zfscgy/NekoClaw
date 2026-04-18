"""Runtime registry for live config/provider references.

The gateway registers the active :class:`Config` and :class:`LLMProvider`
here at startup so that manager utilities (exposed over HTTP by the
NekoChat channel) can mutate them in place and persist the changes.
"""

from __future__ import annotations

from nekoclaw.config.schema import Config
from nekoclaw.providers.base import LLMProvider

_current_config: Config | None = None
_current_provider: LLMProvider | None = None


def set_runtime(config: Config, provider: LLMProvider | None) -> None:
    """Register the active runtime config and provider."""
    global _current_config, _current_provider
    _current_config = config
    _current_provider = provider


def get_config() -> Config | None:
    """Return the active runtime config, if registered."""
    return _current_config


def get_provider() -> LLMProvider | None:
    """Return the active LLM provider, if registered."""
    return _current_provider
