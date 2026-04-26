"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import traceback
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nekoclaw.agent.context import ContextBuilder
from nekoclaw.agent.memory import MemoryStore
from nekoclaw.agent.subagent import SubagentManager
from nekoclaw.agent.tools.cron import CronTool
from nekoclaw.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nekoclaw.agent.tools.message import MessageTool
from nekoclaw.agent.tools.registry import ToolRegistry
from nekoclaw.agent.tools.shell import ExecTool
from nekoclaw.agent.tools.spawn import SpawnTool
from nekoclaw.agent.tools.web import WebFetchTool, WebSearchTool
from nekoclaw.bus.events import InboundMessage, OutboundMessage
from nekoclaw.bus.queue import MessageBus
from nekoclaw.providers.base import LLMProvider, StreamDelta, ToolCallRequest, ToolCallResult, delta_to_openai, is_error_content
from nekoclaw.session.manager import Session, SessionManager


if TYPE_CHECKING:
    from nekoclaw.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        session: Session,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.session = session
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self.context = ContextBuilder(workspace)
        self.sessions = SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            sessions=self.sessions,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._consolidating: set[str] = set()
        self._consolidation_tasks: set[asyncio.Task] = set()
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._processing_lock = asyncio.Lock()
        # session_keys whose currently-running agent loop should stop at the
        # next iteration boundary. Populated by ``user_pause`` inbound messages.
        self._pause_requests: set[str] = set()
        # Per-session in-flight inboxes. While an agent loop is running for a
        # session, additional ``type="user"`` inbound messages are routed here
        # and injected at the next iteration boundary instead of queuing
        # behind ``_processing_lock``.
        self._session_inboxes: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(working_dir=str(self.workspace)))
        self.tools.register(WebSearchTool(max_results=10))
        self.tools.register(WebFetchTool())
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    def _drain_inbox_into_messages(
        self,
        inbox: asyncio.Queue[InboundMessage],
        session: Session,
        messages: list[StreamDelta],
    ) -> bool:
        """Drain any queued in-flight messages for this session and append them
        as ``user`` turns to *messages*. Returns ``True`` if any were injected.
        """
        injected = False
        while not inbox.empty():
            try:
                pending = inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            media_paths = self._save_inbound_media(session.key, pending.media)
            preview = pending.content[:80] + "..." if len(pending.content) > 80 else pending.content
            logger.info(
                "Injecting in-flight message into session {}: {}",
                session.key, preview,
            )
            delta = StreamDelta(
                type="user",
                content=pending.content,
                media=media_paths or [],
            )
            messages.append(delta)
            injected = True
        return injected

    def _release_session_inbox(self, session_key: str) -> None:
        """Deregister the in-flight inbox for *session_key* and re-publish any
        messages that arrived but were never consumed, so they go through the
        normal dispatch path.
        """
        inbox = self._session_inboxes.pop(session_key, None)
        if inbox is None:
            return
        while not inbox.empty():
            try:
                pending = inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            logger.info(
                "Re-publishing unconsumed in-flight message for session {}",
                session_key,
            )
            # Best effort: schedule re-publish without awaiting to avoid
            # blocking callers inside a ``finally`` block.
            asyncio.create_task(self.bus.publish_inbound(pending))

    def _save_inbound_media(self, session_key: str, media: list[str] | None) -> list[str] | None:
        """Persist *media* paths into the session's media directory. Returns
        the list of saved paths (or original paths as a fallback) or ``None``.
        """
        if not media:
            return None
        saved: list[str] = []
        for p in media:
            src = Path(p)
            if not src.is_file():
                logger.warning("Media file not found, skipping: {}", p)
                continue
            try:
                dest = self.sessions.save_media(session_key, src)
                saved.append(str(dest))
            except Exception:
                logger.warning("Failed to save media file {}, using original path", p)
                saved.append(str(src))
        return saved or None

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("send_message_with_attachments", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "send_message_with_attachments" else []))

    async def _run_agent_loop(
        self,
        channel: str,
        chat_id: str,
        session: Session,
    ) -> tuple[str | None, list[str]]:
        """Run the agent iteration loop. Returns (final_content, tools_used).

        Publishes streaming deltas to the message bus and saves the session
        after each tool-call round.
        """
        messages = session.initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        # In-flight inbox is registered by ``_process_message`` (our caller)
        # so cleanup happens even if this loop raises.
        inbox = self._session_inboxes.get(session.key)

        while iteration < self.max_iterations:
            if session.key in self._pause_requests:
                self._pause_requests.discard(session.key)
                logger.info("Agent loop paused by user for session {}", session.key)
                if final_content is None:
                    final_content = "Paused by user."
                break

            if inbox is not None and self._drain_inbox_into_messages(inbox, session, messages):
                self._save_session(session)

            iteration += 1

            openai_messages = delta_to_openai(messages)
            tools_arg = self.tools.get_definitions()

            async def _iter_blocking_deltas(oai_msgs, tools_arg):
                for delta in await self.provider.chat(
                    messages=oai_msgs,
                    tools=tools_arg,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    reasoning_effort=self.reasoning_effort,
                ):
                    yield delta

            async def _iter_effective_deltas(oai_msgs, tools_arg):
                saw_stream_delta = False
                try:
                    async for delta in self.provider.chat_stream(
                        messages=oai_msgs,
                        tools=tools_arg,
                        model=self.model,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        reasoning_effort=self.reasoning_effort,
                    ):
                        saw_stream_delta = True
                        yield delta
                    return
                except Exception as exc:
                    if saw_stream_delta:
                        logger.warning("Streaming interrupted, keeping partial stream output: {}", exc)
                        return
                async for delta in _iter_blocking_deltas(oai_msgs, tools_arg):
                    yield delta

            content_chunks: list[str] = []
            thinking_chunks: list[str] = []
            response_tool_calls: list[ToolCallRequest] = []

            async for delta in _iter_effective_deltas(openai_messages, tools_arg):
                await self.bus.publish_outbound(OutboundMessage(
                    channel=channel, chat_id=chat_id,
                    type="delta", msg=delta,
                ))

                if delta.type == "thinking":
                    if delta.content:
                        thinking_chunks.append(delta.content)
                    continue

                if delta.type == "content":
                    content_chunks.append(delta.content)
                    continue

                if delta.type != "tool_call":
                    logger.warning(f"Unexpected delta type: {delta}")
                    continue

                tc = delta.content
                if isinstance(tc, ToolCallRequest) and not tc.partial:
                    response_tool_calls.append(tc)

            response_content = "".join(content_chunks) if content_chunks else None
            response_thinking = "".join(thinking_chunks).strip() or None

            if response_thinking:
                messages.append(StreamDelta(type="thinking", content=response_thinking))

            if response_tool_calls:
                if response_content:
                    messages.append(StreamDelta(type="content", content=response_content))

                for tc in response_tool_calls:
                    messages.append(StreamDelta(type="tool_call", content=tc))

                tool_results: list[ToolCallResult] = []
                for tool_call in response_tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    try:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    except Exception as exc:
                        tb_lines = traceback.format_exc().splitlines()
                        short_tb = "\n".join(tb_lines[-20:])
                        result = f"[ERROR] {type(exc).__name__}: {exc}\n\nTraceback:\n{short_tb}"
                        logger.error("Tool {} raised exception: {}", tool_call.name, exc)
                    tool_results.append(ToolCallResult(
                        tool_call_id=tool_call.id, name=tool_call.name, content=result,
                    ))
                messages.append(StreamDelta(type="tool_call_results", content=tool_results))

                await self.bus.publish_outbound(OutboundMessage(
                    channel=channel, chat_id=chat_id,
                    type="delta",
                    msg=StreamDelta(type="tool_call_results", content=tool_results),
                ))

                self._save_session(session)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=channel, chat_id=chat_id,
                    type="clear_unsent_buffer",
                ))

                # If the message tool was called, the current round is over, waiting for user request...
                if "send_message_with_attachments" in tools_used:
                    break
                continue
            else:
                if is_error_content(response_content):
                    logger.error("LLM returned error: {}", (response_content or "")[:200])
                    final_content = response_content or "Sorry, I encountered an error calling the AI model."
                    break
                if response_content is None:
                    logger.warning(
                        "LLM returned empty content (content={!r}); "
                        "treating as no response",
                        response_content,
                    )
                if response_content:
                    messages.append(StreamDelta(
                        type="content",
                        content=response_content,
                        time=datetime.now(timezone.utc).isoformat(),
                    ))
                final_content = response_content
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        self._save_session(session)
        return final_content, tools_used

    async def run(self) -> None:
        """Run the agent loop, dispatching inbound messages as concurrent tasks."""
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.type == "user_pause":
                # Don't dispatch as a regular message: just flag the session so
                # the currently-running ``_run_agent_loop`` exits at its next
                # iteration boundary.
                self._pause_requests.add(msg.session_key)
                logger.info("Pause requested for session {}", msg.session_key)
                continue

            # If an agent loop is already running for this session, route
            # regular user messages to its in-flight inbox so they can be
            # picked up at the next iteration boundary instead of queuing
            # behind ``_processing_lock``.
            if msg.type == "user" and (inbox := self._session_inboxes.get(msg.session_key)):
                await inbox.put(msg)
                logger.info(
                    "Routed in-flight message to running loop for session {}",
                    msg.session_key,
                )
                continue

            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, type="stream_end",
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                await self._process_message(msg)
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                tb_lines = traceback.format_exc().splitlines()
                short_tb = "\n".join(tb_lines[-15:])
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    msg=StreamDelta(type="content", content=f"An error occurred:\n{short_tb}"),
                    metadata={"_error": True},
                ))
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id, type="stream_end",
                ))

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
    ) -> str:
        """Process a single inbound message. Returns the final text content."""
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
        else:
            preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
            logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)
            channel, chat_id = msg.channel, msg.chat_id
            key = session_key or msg.session_key

        session = self.sessions.get_or_create(key)

        if msg.channel != "system":
            unconsolidated = len(session.messages) - session.last_consolidated
            if unconsolidated >= self.memory_window and session.key not in self._consolidating:
                self._consolidating.add(session.key)
                lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

                async def _consolidate_and_unlock():
                    try:
                        async with lock:
                            await self._consolidate_memory(session)
                    finally:
                        self._consolidating.discard(session.key)
                        _task = asyncio.current_task()
                        if _task is not None:
                            self._consolidation_tasks.discard(_task)

                _task = asyncio.create_task(_consolidate_and_unlock())
                self._consolidation_tasks.add(_task)

        # Tag session with subagent reference so _save_session can replace the
        # user message (announce text) with a subagent_ref delta on disk.
        subagent_meta = msg.metadata if msg.metadata.get("subagent_session_id") else None
        session._pending_subagent_ref = subagent_meta  # type: ignore[attr-defined]

        self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("send_message_with_attachments"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        media_paths = self._save_inbound_media(key, msg.media)

        history = session.get_history(max_messages=self.memory_window)
        session.initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=media_paths,
            channel=channel, chat_id=chat_id,
        )
        # Skip system prompt + already-persisted history when flushing to session.messages
        session._save_offset = len(session.initial_messages) - 1

        # Tag the user StreamDelta with its saved media paths so _save_session
        # can persist them for UI replay without re-embedding raw base64 blobs.
        if media_paths:
            session.initial_messages[session._save_offset].media = media_paths

        # Register an in-flight inbox so follow-up user messages arriving
        # while the agent loop is running can be injected at the next
        # iteration boundary instead of queuing behind ``_processing_lock``.
        self._session_inboxes[session.key] = asyncio.Queue()
        try:
            final_content, _ = await self._run_agent_loop(
                channel=channel,
                chat_id=chat_id,
                session=session,
            )
        finally:
            self._release_session_inbox(session.key)

        if final_content is None:
            final_content = ("Background task completed." if msg.channel == "system"
                             else "I've completed processing but have no response to give.")
            session.initial_messages.append(StreamDelta(
                type="content",
                content=final_content,
                time=datetime.now(timezone.utc).isoformat(),
            ))
            self._save_session(session)

        await self.bus.publish_outbound(OutboundMessage(
            channel=channel, chat_id=chat_id, type="stream_end",
        ))
        return final_content

    def _save_session(self, session: Session) -> None:
        """Append new StreamDeltas from session.initial_messages to the session and persist to disk."""
        # Check for pending subagent reference (set by _process_message for subagent announcements)
        sub_ref: dict | None = getattr(session, "_pending_subagent_ref", None)
        sub_ref_emitted = False

        messages = session.initial_messages
        for delta in messages[session._save_offset:]:
            if delta.type == "system":
                continue

            if delta.type == "user":
                # If this is a subagent announcement, emit a subagent_ref instead.
                # The announce content (with the full result) is on the delta itself.
                if sub_ref and not sub_ref_emitted:
                    raw = delta.content
                    if isinstance(raw, str) and raw.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                        raw = raw.split("\n\n", 1)[-1]
                    session.messages.append(StreamDelta(
                        type="subagent_ref",
                        content={
                            "session_id": sub_ref["subagent_session_id"],
                            "label": sub_ref.get("subagent_label", ""),
                            "status": sub_ref.get("subagent_status", "ok"),
                            "task": sub_ref.get("subagent_task", ""),
                            "announce": raw,
                        },
                    ))
                    sub_ref_emitted = True
                    continue

                content = delta.content
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        content = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if (c.get("type") == "text"
                                and isinstance(c.get("text"), str)
                                and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)):
                            continue
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    content = filtered
                session.messages.append(StreamDelta(type="user", content=content, media=delta.media))
                continue

            session.messages.append(delta)

        session._save_offset = len(messages)
        session.updated_at = datetime.now()
        self.sessions.save(session)

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(self.workspace).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        return await self._process_message(msg, session_key=session_key)
