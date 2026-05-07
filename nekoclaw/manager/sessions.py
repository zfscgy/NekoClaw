"""Runtime session management helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nekoclaw.config.manager import get_agent, get_global_config
from nekoclaw.session.manager import SessionManager


def _workspace() -> Path:
    return get_global_config().workspace_path


def _session_manager() -> SessionManager:
    agent = get_agent()
    sessions = getattr(agent, "sessions", None)
    if isinstance(sessions, SessionManager):
        return sessions
    return SessionManager(_workspace())


def delete_session(key: str) -> dict[str, Any]:
    """Move a session JSONL file to the session bin folder."""
    moved_to = _session_manager().delete_session(key)
    return {
        "key": key,
        "deleted": moved_to is not None,
        "moved_to": str(moved_to) if moved_to else None,
    }
