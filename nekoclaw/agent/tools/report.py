"""Tool for subagents to report task completion to the main agent."""

from typing import Any

from nekoclaw.agent.tools.base import Tool


class ReportTaskTool(Tool):
    """Tool used by subagents to provide their final task report."""

    def __init__(self) -> None:
        self.report: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return "ReportTask"

    @property
    def description(self) -> str:
        return (
            "Report the final task status and output to the main agent. "
            "Subagents must call this tool when the assigned task is finished "
            "or cannot be completed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "success": {
                    "type": "boolean",
                    "description": "Whether the assigned task was completed successfully.",
                },
                "output": {
                    "type": "string",
                    "description": "The main result or response for the main agent.",
                },
                "actions": {
                    "type": "string",
                    "description": "The crucial actions taken to finish this task.",
                },
                "products": {
                    "type": "string",
                    "description": "Artifacts produced by this subagent, such as files written.",
                },
            },
            "required": ["success", "output", "actions", "products"],
        }

    async def execute(
        self,
        success: bool,
        output: str,
        actions: str,
        products: str,
        **kwargs: Any,
    ) -> str:
        self.report = {
            "success": success,
            "output": output,
            "actions": actions,
            "products": products,
        }
        return "ReportTask received. The task report will be sent to the main agent."
