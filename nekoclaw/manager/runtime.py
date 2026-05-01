"""Runtime registry for live config/provider references.

The gateway registers the active :class:`Config` and :class:`LLMProvider`
here at startup so that manager utilities (exposed over HTTP by the
NekoChat channel) can mutate them in place and persist the changes.
"""

from __future__ import annotations

from typing import Any

from nekoclaw.config.schema import Config
from nekoclaw.providers.base import LLMProvider

_current_config: Config | None = None
_current_provider: LLMProvider | None = None
_current_agent: Any | None = None
_current_heartbeat: Any | None = None
_UNSET = object()


def set_runtime(
    config: Config,
    provider: LLMProvider | None,
    *,
    agent: Any = _UNSET,
    heartbeat: Any = _UNSET,
) -> None:
    """Register the active runtime config and live service references."""
    global _current_config, _current_provider, _current_agent, _current_heartbeat
    _current_config = config
    _current_provider = provider
    if agent is not _UNSET:
        _current_agent = agent
    if heartbeat is not _UNSET:
        _current_heartbeat = heartbeat


def get_config() -> Config | None:
    """Return the active runtime config, if registered."""
    return _current_config


def get_provider() -> LLMProvider | None:
    """Return the active LLM provider, if registered."""
    return _current_provider


def get_agent() -> Any | None:
    """Return the active agent loop, if registered."""
    return _current_agent


def get_heartbeat() -> Any | None:
    """Return the active heartbeat service, if registered."""
    return _current_heartbeat
