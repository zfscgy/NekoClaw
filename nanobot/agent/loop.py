"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, StreamDelta, ToolCallRequest, is_error_content
from nanobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig
    from nanobot.cron.service import CronService


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
        channels_config: ChannelsConfig | None = None,
    ):
        self.bus = bus
        self.channels_config = channels_config
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
        from nanobot.agent.tools.mcp import connect_mcp_servers
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

    @staticmethod
    def _tool_call_from_payload(payload: dict[str, Any]) -> ToolCallRequest | None:
        tool_id = str(payload.get("id") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not tool_id or not name or "arguments" not in payload:
            return None

        arguments = payload.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                return None
        elif arguments is None:
            arguments = {}
        elif not isinstance(arguments, dict):
            return None

        return ToolCallRequest(id=tool_id, name=name, arguments=arguments)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_delta: Callable[[StreamDelta], Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages)."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            async def _iter_blocking_deltas(tools_arg):
                for delta in await self.provider.chat(
                    messages=messages,
                    tools=tools_arg,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    reasoning_effort=self.reasoning_effort,
                ):
                    yield delta

            async def _iter_effective_deltas(tools_arg):
                if on_delta is not None:
                    saw_stream_delta = False
                    try:
                        async for delta in self.provider.chat_stream(
                            messages=messages,
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
                async for delta in _iter_blocking_deltas(tools_arg):
                    yield delta

            tools_arg = self.tools.get_definitions()
            content_chunks: list[str] = []
            response_tool_calls: list[ToolCallRequest] = []
            seen_tool_call_keys: set[tuple[str, str, str]] = set()
            response_thinking: str | None = None
            thinking_chunks: list[str] = []
            partial_tool_calls: dict[str, dict[str, Any]] = {}
            raw_tool_call_buffer = ""

            def _append_tool_call(tool_call: ToolCallRequest) -> None:
                key = (
                    tool_call.id,
                    tool_call.name,
                    json.dumps(tool_call.arguments, sort_keys=True, ensure_ascii=False),
                )
                if key in seen_tool_call_keys:
                    return
                seen_tool_call_keys.add(key)
                response_tool_calls.append(tool_call)

            async for delta in _iter_effective_deltas(tools_arg):
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
                    continue

                raw_chunk = delta.content or ""
                if not raw_chunk:
                    continue

                try:
                    payload = json.loads(raw_chunk)
                except Exception:
                    raw_tool_call_buffer += raw_chunk
                    try:
                        payload = json.loads(raw_tool_call_buffer)
                    except Exception:
                        continue
                    raw_tool_call_buffer = ""
                else:
                    raw_tool_call_buffer = ""

                if isinstance(payload, dict) and payload.get("partial"):
                    key = str(payload.get("index", payload.get("id") or len(partial_tool_calls)))
                    state = partial_tool_calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
                    if payload.get("id"):
                        state["id"] = payload["id"]
                    if payload.get("name"):
                        state["name"] = self._merge_partial_text(str(state.get("name", "")), str(payload["name"]))
                    if "arguments" in payload and payload.get("arguments") is not None:
                        state["arguments"] = self._merge_partial_text(
                            str(state.get("arguments", "")),
                            str(payload["arguments"]),
                        )
                    tool_call = self._tool_call_from_payload(state)
                    if tool_call is not None:
                        _append_tool_call(tool_call)
                        partial_tool_calls.pop(key, None)
                    continue

                payload_items = payload if isinstance(payload, list) else [payload]
                for item in payload_items:
                    if not isinstance(item, dict):
                        continue
                    tool_call = self._tool_call_from_payload(item)
                    if tool_call is None:
                        logger.warning(
                            "Dropping incomplete tool call delta: {}",
                            json.dumps(item, ensure_ascii=False)[:200],
                        )
                        continue
                    _append_tool_call(tool_call)
                    if tool_call.id:
                        for partial_key, partial_state in list(partial_tool_calls.items()):
                            if partial_state.get("id") == tool_call.id:
                                partial_tool_calls.pop(partial_key, None)

            for key in sorted(partial_tool_calls.keys()):
                tool_call = self._tool_call_from_payload(partial_tool_calls[key])
                if tool_call is None:
                    logger.warning(
                        "Dropping incomplete tool call delta: {}",
                        json.dumps(partial_tool_calls[key], ensure_ascii=False)[:200],
                    )
                    continue
                _append_tool_call(tool_call)

            if raw_tool_call_buffer.strip():
                logger.warning("Dropping incomplete raw tool call delta: {}", raw_tool_call_buffer[:200])

            response_content = "".join(content_chunks) if content_chunks else None
            response_thinking = "".join(thinking_chunks).strip() or None
            assistant_reasoning = None if on_delta is not None else response_thinking

            if response_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response_tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response_content, tool_call_dicts,
                    reasoning_content=assistant_reasoning,
                )

                for tool_call in response_tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                if self._message_was_sent():
                    break
                continue
            else:
                clean = self._strip_think(response_content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
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
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=assistant_reasoning,
                )
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
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
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
        # System messages: parse origin from chat_id ("channel:chat_id")
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
            final_content, _, all_msgs = await self._run_agent_loop(messages)
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

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
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 nanobot commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands")

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

        _is_streaming = (msg.metadata or {}).get("_streaming")
        ch = self.channels_config
        _thinking_rounds: list[str] = []
        _current_thinking: list[str] = []

        async def _bus_delta(delta: StreamDelta) -> None:
            meta = dict(msg.metadata or {})
            if delta.type == "content":
                if _is_streaming:
                    meta["_stream_token"] = True
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id, content=delta.content, metadata=meta,
                    ))
                return

            if delta.type == "thinking":
                if delta.content:
                    _current_thinking.append(delta.content)
                if ch and not ch.send_progress:
                    return
                if _is_streaming:
                    meta["_stream_think"] = True
                else:
                    meta["_progress"] = True
                    meta["_tool_hint"] = False
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id, content=delta.content, metadata=meta,
                ))
                return

            if delta.type != "tool_call":
                return

            try:
                payload = json.loads(delta.content)
            except Exception:
                if _is_streaming:
                    meta["_stream_tool_delta"] = True
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id, content=delta.content, metadata=meta,
                    ))
                return

            items = payload if isinstance(payload, list) else [payload]
            complete_tool_calls: list[ToolCallRequest] = []
            has_partial = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("partial"):
                    has_partial = True
                    continue
                tool_call = self._tool_call_from_payload(item)
                if tool_call is not None:
                    complete_tool_calls.append(tool_call)
                else:
                    has_partial = True

            if has_partial and _is_streaming:
                meta["_stream_tool_delta"] = True
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id, content=delta.content, metadata=meta,
                ))

            if complete_tool_calls and _current_thinking:
                _thinking_rounds.append("".join(_current_thinking).strip())
                _current_thinking.clear()

            if complete_tool_calls:
                if ch and not ch.send_tool_hints:
                    return
                meta["_progress"] = True
                meta["_tool_hint"] = True
                for tc in complete_tool_calls:
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=self._format_tool_call(tc),
                        metadata=meta,
                    ))

        _effective_on_delta = on_delta or _bus_delta

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages,
            on_delta=_effective_on_delta,
        )

        if _current_thinking:
            _thinking_rounds.append("".join(_current_thinking).strip())

        message_sent = self._message_was_sent()

        if final_content is None and not message_sent:
            final_content = "I've completed processing but have no response to give."
            all_msgs = list(all_msgs)
            all_msgs.append({"role": "assistant", "content": final_content})

        self._save_turn(session, all_msgs, 1 + len(history),
                        thinking_rounds=_thinking_rounds or None)
        self.sessions.save(session)

        if message_sent:
            # Session is now on disk — signal the channel to discard its streaming
            # replay buffers.  Without this, NanochatChannel would never clear
            # _active_streams / _stream_segments for turns that ended via the
            # message tool, causing reconnecting clients to see a stale replay.
            meta_done = dict(msg.metadata or {})
            meta_done["_stream_done"] = True
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="", metadata=meta_done,
            ))
            return None

        if _is_streaming and final_content is not None:
            # Content was already delivered token-by-token via stream_content_delta.
            # Suppress the redundant assembled message and let the channel know the
            # stream is done so it can flush the frontend's live panel.
            meta_end = dict(msg.metadata or {})
            meta_end["_stream_end"] = True
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="", metadata=meta_end,
            ))
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=dict(msg.metadata or {}),
        )

    _SESSION_ENTRY_KEYS = frozenset({
        "role", "content", "tool_calls", "tool_call_id", "name",
        "_ui_only", "reasoning_content", "thinking_blocks",
    })

    def _save_turn(
        self,
        session: Session,
        messages: list[dict],
        skip: int,
        thinking_rounds: list[str] | None = None,
    ) -> None:
        """Save new-turn messages into session.

        thinking_rounds is an ordered list of thinking-text blocks, one per LLM
        call round.  Each block is inserted into the session as a ``_ui_only``
        entry immediately before the corresponding assistant message so the UI
        can replay it without ever sending it back to the LLM.
        """
        from datetime import datetime
        thinking_iter = iter(thinking_rounds or [])
        for m in messages[skip:]:
            role, content = m.get("role"), m.get("content")
            if role == "assistant" and not content and not m.get("tool_calls"):
                continue
            entry = {k: m[k] for k in self._SESSION_ENTRY_KEYS if k in m}
            if role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            continue
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            if role == "assistant" and thinking_rounds is not None:
                think = next(thinking_iter, None)
                if think:
                    session.messages.append({
                        "role": "assistant",
                        "content": think,
                        "_ui_only": True,
                    })
            if role == "assistant" and entry.get("content") and entry.get("tool_calls"):
                entry["_ui_segments"] = ["content", "tool_calls"]
            session.messages.append(entry)
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
