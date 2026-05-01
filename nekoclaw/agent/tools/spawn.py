"""Tool for calling background subagents."""

from typing import TYPE_CHECKING, Any

from nekoclaw.agent.tools.base import Tool

if TYPE_CHECKING:
    from nekoclaw.agent.subagent import SubagentManager


class CallSubagentTool(Tool):
    """Tool to delegate work to a background subagent."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = f"{channel}:{chat_id}"

    @property
    def name(self) -> str:
        return "call_subagent"

    @property
    def description(self) -> str:
        return (
            "Call a background subagent to complete a focused task independently. "
            "Use this for complex, research-heavy, or time-consuming work that can run "
            "without blocking the main conversation. The subagent is required to finish "
            "by calling ReportTask with success status, output, actions taken, and "
            "products produced; that report will be returned to the main agent."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, label: str | None = None, **kwargs: Any) -> str:
        """Call a subagent to execute the given task."""
        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
        )
