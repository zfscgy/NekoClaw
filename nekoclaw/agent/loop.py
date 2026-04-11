"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import traceback
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

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
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        web_proxy: str | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.web_proxy = web_proxy
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            web_proxy=web_proxy,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()
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

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nekoclaw.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("send_message_with_attachments", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "send_message_with_attachments" else []))

    def _message_was_sent(self) -> bool:
        """Return True if the message tool fired during this turn."""
        mt = self.tools.get("send_message_with_attachments")
        return isinstance(mt, MessageTool) and mt._sent_in_turn

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _format_tool_call(tc) -> str:
        """Format a single tool call as name(arg1=val1, arg2=val2)."""
        args = tc.arguments or {}
        if isinstance(args, str):
            return f"{tc.name}({args})" if args else tc.name + "()"
        if isinstance(args, list):
            args = {}
        if not args:
            return tc.name + "()"
        parts = []
        for k, v in args.items():
            if isinstance(v, str):
                s = repr(v) if len(v) <= 60 else repr(v[:57] + "...")
            elif isinstance(v, (int, float, bool)):
                s = str(v)
            elif v is None:
                s = "None"
            else:
                s = repr(v) if len(repr(v)) <= 60 else repr(v)[:57] + "..."
            parts.append(f"{k}={s}")
        return f"{tc.name}({', '.join(parts)})"

    @staticmethod
    def _merge_partial_text(current: str, incoming: str) -> str:
        if not incoming:
            return current
        if not current:
            return incoming
        if incoming.startswith(current):
            return incoming
        return current + incoming


    async def _run_agent_loop(
        self,
        initial_messages: list[StreamDelta],
        on_delta: Callable[[StreamDelta], Awaitable[None]] | None = None,
        on_after_tool_call: Callable[[list[StreamDelta]], Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[StreamDelta]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages).

        ``messages`` is a flat list of ``StreamDelta`` objects throughout the
        loop.  Conversion to OpenAI format is done only when calling the
        provider, via ``delta_to_openai``.
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            openai_messages = delta_to_openai(messages)
            tools_arg = self.tools.get_definitions()

            ###################################################
            # Non-streaming and streaming iteration definitions
            ###################################################
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
                if on_delta is not None:
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
            ###################################################

            content_chunks: list[str] = []
            thinking_chunks: list[str] = []
            response_tool_calls: list[ToolCallRequest] = []

            async for delta in _iter_effective_deltas(openai_messages, tools_arg):
                if on_delta is not None:
                    await on_delta(delta)

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

            # Append the assistant turn as StreamDeltas (thinking is preserved
            # in the list for session storage but excluded by delta_to_openai).
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

                if on_after_tool_call is not None:
                    await on_after_tool_call(messages)
                if self._message_was_sent():
                    break
                continue
            else:
                clean = self._strip_think(response_content)
                if is_error_content(clean):
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                if clean is None:
                    logger.warning(
                        "LLM returned empty content (content={!r}); "
                        "treating as no response",
                        response_content,
                    )
                if clean:
                    messages.append(StreamDelta(type="content", content=clean))
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        text = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            msg=StreamDelta(type="content", content=text),
        ))
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, type="stream_end",
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=response.channel, chat_id=response.chat_id,
                        type="stream_end",
                    ))
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

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_delta: Callable[[StreamDelta], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=self.memory_window)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
            )
            _sys_skip = [1 + len(history)]

            async def _save_progress_sys(msgs: list[StreamDelta]) -> None:
                self._save_turn(session, msgs, _sys_skip[0])
                self.sessions.save(session)
                _sys_skip[0] = len(msgs)

            final_content, _, all_msgs = await self._run_agent_loop(
                messages, on_after_tool_call=_save_progress_sys,
            )
            self._save_turn(session, all_msgs, _sys_skip[0])
            self.sessions.save(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  msg=StreamDelta(type="content", content=final_content or "Background task completed."))

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                msg=StreamDelta(type="content", content="Memory archival failed, session not cleared. Please try again."),
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    msg=StreamDelta(type="content", content="Memory archival failed, session not cleared. Please try again."),
                )
            finally:
                self._consolidating.discard(session.key)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  msg=StreamDelta(type="content", content="New session started."))
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  msg=StreamDelta(type="content", content="🐈 nekoclaw commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands"))

        unconsolidated = len(session.messages) - session.last_consolidated
        if (unconsolidated >= self.memory_window and session.key not in self._consolidating):
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

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("send_message_with_attachments"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=self.memory_window)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )
        _skip = [1 + len(history)]

        async def _save_progress(msgs: list[StreamDelta]) -> None:
            self._save_turn(session, msgs, _skip[0])
            self.sessions.save(session)
            _skip[0] = len(msgs)
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                type="clear_unsent_buffer",
            ))

        async def _bus_delta(delta: StreamDelta) -> None:
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                type="delta", msg=delta,
            ))

        _effective_on_delta = on_delta or _bus_delta

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages,
            on_delta=_effective_on_delta,
            on_after_tool_call=_save_progress,
        )

        message_sent = self._message_was_sent()

        if final_content is None and not message_sent:
            final_content = "I've completed processing but have no response to give."
            all_msgs = list(all_msgs)
            all_msgs.append(StreamDelta(type="content", content=final_content))

        self._save_turn(session, all_msgs, _skip[0])
        self.sessions.save(session)

        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            type="stream_end",
        ))
        return None

    def _save_turn(
        self,
        session: Session,
        messages: list[StreamDelta],
        skip: int,
    ) -> None:
        """Save new-turn StreamDeltas into session storage.

        System deltas are skipped (they are rebuilt each turn).  User deltas
        have their runtime-context prefix stripped before persistence.
        """
        from datetime import datetime

        for delta in messages[skip:]:
            if delta.type == "system":
                continue

            if delta.type == "user":
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
                session.messages.append(StreamDelta(type="user", content=content))
                continue

            session.messages.append(delta)

        session.updated_at = datetime.now()

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
        on_delta: Callable[[StreamDelta], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(
            msg, session_key=session_key, on_delta=on_delta,
        )
        return response.content if response else ""
