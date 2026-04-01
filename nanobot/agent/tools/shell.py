"""Shell execution tool."""

import os
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.tools.terminal import PersistentShell


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
            "variables persists between calls. Use with caution."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": (
                        "Optional directory to cd into before running the command. "
                        "The shell's cwd will remain there for subsequent calls."
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
