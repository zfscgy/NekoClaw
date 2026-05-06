"""Startup helpers for the nekoclaw gateway.

These helpers split bootstrap concerns out of ``__main__.py``:

- :func:`load_runtime_config` ensures config files exist on disk and prompts
  the user for any required keys that have not been set yet.
- :func:`ensure_exec_tool_python_venv` prepares the bundled Windows venv used
  by the ``exec`` tool (idempotent — safe to call on every launch).
- :func:`sync_optional_skills` copies the bundled ``nekoclaw/skills/optional``
  skills into the user's workspace ``skills/`` directory on first run, so
  they appear as workspace-managed (editable, toggleable) skills.
"""

from nekoclaw.startup.config import (
    load_runtime_config,
    missing_gateway_config_keys,
)
from nekoclaw.startup.python_env import ensure_exec_tool_python_venv
from nekoclaw.startup.skills import sync_optional_skills

__all__ = [
    "ensure_exec_tool_python_venv",
    "load_runtime_config",
    "missing_gateway_config_keys",
    "sync_optional_skills",
]
