"""Subagent manager for background task execution."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nekoclaw.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nekoclaw.agent.tools.registry import ToolRegistry
from nekoclaw.agent.tools.shell import ExecTool
from nekoclaw.agent.tools.web import WebFetchTool, WebSearchTool
from nekoclaw.bus.events import InboundMessage, OutboundMessage
from nekoclaw.bus.queue import MessageBus
from nekoclaw.providers.base import LLMProvider, StreamDelta, ToolCallRequest, ToolCallResult, parse_stream_deltas
from nekoclaw.session.manager import Session, SessionManager


class SubagentManager:
    """Manages background subagent execution with persistent sessions."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        sessions: SessionManager,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.sessions = sessions
        self.model = model or provider.get_default_model()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.restrict_to_workspace = restrict_to_workspace
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return (
            f"Subagent [{display_label}] started "
            f"(id: {task_id}, session_id: subagent:{task_id}). "
            "I'll notify you when it completes."
        )

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """Execute the subagent task with persistent session and streaming."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)
        channel = origin["channel"]
        chat_id = origin["chat_id"]
        sub_meta = {"subagent_id": task_id, "subagent_label": label}

        # Create a persistent session for this subagent
        session_key = f"subagent:{task_id}"
        session = self.sessions.get_or_create(session_key)
        session.metadata["parent_channel"] = channel
        session.metadata["parent_chat_id"] = chat_id
        session.metadata["label"] = label
        session.metadata["task"] = task

        # Notify frontend that a subagent has started
        await self.bus.publish_outbound(OutboundMessage(
            channel=channel, chat_id=chat_id,
            type="stream_start",
            metadata={**sub_meta, "subagent_status": "running"},
        ))

        try:
            tools = ToolRegistry()
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ExecTool(working_dir=str(self.workspace)))
            tools.register(WebSearchTool())
            tools.register(WebFetchTool())

            system_prompt = self._build_subagent_prompt()
            messages: list[StreamDelta] = [
                StreamDelta(type="system", content=system_prompt),
                StreamDelta(type="user", content=task),
            ]

            # Persist the initial user message
            session.messages.append(StreamDelta(type="user", content=task))
            self.sessions.save(session)

            max_iterations = 15
            iteration = 0
            final_result: str | None = None

            while iteration < max_iterations:
                iteration += 1

                from nekoclaw.providers.base import delta_to_openai
                openai_messages = delta_to_openai(messages)

                content_chunks: list[str] = []
                thinking_chunks: list[str] = []
                response_tool_calls: list[ToolCallRequest] = []

                # Try streaming first, fall back to blocking
                try:
                    async for delta in self.provider.chat_stream(
                        messages=openai_messages,
                        tools=tools.get_definitions(),
                        model=self.model,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        reasoning_effort=self.reasoning_effort,
                    ):
                        # Stream delta to the frontend
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel, chat_id=chat_id,
                            type="delta", msg=delta,
                            metadata=sub_meta,
                        ))

                        if delta.type == "thinking":
                            if delta.content:
                                thinking_chunks.append(delta.content)
                        elif delta.type == "content":
                            content_chunks.append(delta.content)
                        elif delta.type == "tool_call":
                            tc = delta.content
                            if isinstance(tc, ToolCallRequest) and not tc.partial:
                                response_tool_calls.append(tc)
                except Exception:
                    # Fall back to non-streaming
                    content_chunks.clear()
                    thinking_chunks.clear()
                    response_tool_calls.clear()
                    deltas = await self.provider.chat(
                        messages=openai_messages,
                        tools=tools.get_definitions(),
                        model=self.model,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        reasoning_effort=self.reasoning_effort,
                    )
                    for delta in deltas:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=channel, chat_id=chat_id,
                            type="delta", msg=delta,
                            metadata=sub_meta,
                        ))
                    content_text, tool_calls, thinking_text = parse_stream_deltas(deltas)
                    if content_text:
                        content_chunks.append(content_text)
                    if thinking_text:
                        thinking_chunks.append(thinking_text)
                    response_tool_calls = tool_calls

                response_content = "".join(content_chunks) if content_chunks else None
                response_thinking = "".join(thinking_chunks).strip() or None

                if response_thinking:
                    messages.append(StreamDelta(type="thinking", content=response_thinking))
                    session.messages.append(StreamDelta(type="thinking", content=response_thinking))

                if response_tool_calls:
                    if response_content:
                        messages.append(StreamDelta(type="content", content=response_content))
                        session.messages.append(StreamDelta(type="content", content=response_content))
                    for tc in response_tool_calls:
                        messages.append(StreamDelta(type="tool_call", content=tc))
                        session.messages.append(StreamDelta(type="tool_call", content=tc))

                    tool_results: list[ToolCallResult] = []
                    for tool_call in response_tool_calls:
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        logger.debug("Subagent [{}] executing: {} with arguments: {}", task_id, tool_call.name, args_str)
                        try:
                            result = await tools.execute(tool_call.name, tool_call.arguments)
                        except Exception as exc:
                            result = f"[ERROR] {type(exc).__name__}: {exc}"
                            logger.error("Subagent [{}] tool {} raised: {}", task_id, tool_call.name, exc)
                        tool_results.append(ToolCallResult(
                            tool_call_id=tool_call.id, name=tool_call.name, content=result,
                        ))
                    messages.append(StreamDelta(type="tool_call_results", content=tool_results))
                    session.messages.append(StreamDelta(type="tool_call_results", content=tool_results))

                    await self.bus.publish_outbound(OutboundMessage(
                        channel=channel, chat_id=chat_id,
                        type="delta",
                        msg=StreamDelta(type="tool_call_results", content=tool_results),
                        metadata=sub_meta,
                    ))

                    self.sessions.save(session)
                    # Signal the frontend to commit this round
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=channel, chat_id=chat_id,
                        type="clear_unsent_buffer",
                        metadata=sub_meta,
                    ))
                    continue
                else:
                    if response_content:
                        messages.append(StreamDelta(type="content", content=response_content))
                        session.messages.append(StreamDelta(type="content", content=response_content))
                    final_result = response_content
                    break

            if final_result is None:
                final_result = "Task completed but no final response was generated."

            self.sessions.save(session)
            logger.info("Subagent [{}] completed successfully", task_id)

            # Notify frontend that subagent finished
            await self.bus.publish_outbound(OutboundMessage(
                channel=channel, chat_id=chat_id,
                type="stream_end",
                metadata={**sub_meta, "subagent_status": "ok"},
            ))

            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error("Subagent [{}] failed: {}", task_id, e)

            session.messages.append(StreamDelta(type="content", content=error_msg))
            self.sessions.save(session)

            await self.bus.publish_outbound(OutboundMessage(
                channel=channel, chat_id=chat_id,
                type="stream_end",
                metadata={**sub_meta, "subagent_status": "error"},
            ))

            await self._announce_result(task_id, label, task, error_msg, origin, "error")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"
        session_key = f"subagent:{task_id}"

        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            metadata={
                "subagent_session_id": session_key,
                "subagent_label": label,
                "subagent_status": status,
                "subagent_task": task,
            },
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    def _build_subagent_prompt(self) -> str:
        """Build a focused system prompt for the subagent."""
        from nekoclaw.agent.context import ContextBuilder
        from nekoclaw.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        parts = [f"""# Subagent

{time_ctx}

You are a subagent spawned by the main agent to complete a specific task.
Stay focused on the assigned task. Your final response will be reported back to the main agent.

## Workspace
{self.workspace}"""]

        skills_summary = SkillsLoader(self.workspace).build_skills_summary()
        if skills_summary:
            parts.append(f"## Skills\n\nRead SKILL.md with read_file to use a skill.\n\n{skills_summary}")

        return "\n\n".join(parts)

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
