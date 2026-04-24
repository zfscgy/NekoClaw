"""Shell execution tool."""

import os
from typing import Any

from nekoclaw.agent.tools.base import Tool
from nekoclaw.tools.terminal import PersistentShell


class ExecTool(Tool):
    """Tool to execute shell commands inside a persistent shell session."""

    def __init__(self, working_dir: str | None = None):
        self.working_dir = working_dir
        self._shell = PersistentShell(cwd=working_dir or os.getcwd())

    # ------------------------------------------------------------------
    # Tool interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command inside a persistent session and return its output. "
            "State such as the current directory, activated Python environments, and exported "
            "variables persists between calls. The `timeout` is an idle timeout: as long as "
            "the command keeps writing to stdout or stderr it will keep running, and it only "
            "fires when the process has been silent for that many seconds. "
            "Do NOT launch interactive programs that take over stdin (e.g. bare `python`, "
            "`ipython`, `vim`, `ssh` without a command); they will hang until the idle "
            "timeout and then the shell will be auto-restarted. Use non-interactive forms "
            "such as `python -c '...'` or `python script.py` instead. "
            "Special command `:reset` kills the current shell and starts a new one with "
            "the same profile commands re-run; use it after an accidental hang or to clear "
            "a polluted environment."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The shell command to execute. "
                        "Pass the literal string `:reset` to restart the underlying shell "
                        "session (re-runs profile commands, clears cwd/env state)."
                    ),
                },
                "working_dir": {
                    "type": "string",
                    "description": (
                        "Optional directory to cd into before running the command. "
                        "The shell's cwd will remain there for subsequent calls. "
                        "Ignored when `command` is `:reset`."
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        return await self._shell.execute(command=command, working_dir=working_dir)

    async def close(self) -> None:
        """Shut down the persistent shell process."""
        await self._shell.close()
