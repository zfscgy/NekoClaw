"""Runtime session management helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nekoclaw.config.loader import load_config
from nekoclaw.manager.runtime import get_agent
from nekoclaw.manager.runtime import get_config as _get_runtime_config
from nekoclaw.session.manager import SessionManager


def _workspace() -> Path:
    cfg = _get_runtime_config() or load_config()
    return cfg.workspace_path


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
