"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

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
from nanobot.providers.base import LLMProvider, ToolCallRequest
from nanobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig
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

    _TOOL_RESULT_MAX_CHARS = 500

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
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig
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
        self.exec_config = exec_config or ExecToolConfig()
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
            exec_config=self.exec_config,
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
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(max_results=10))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
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
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    def _message_was_sent(self) -> bool:
        """Return True if the message tool fired during this turn."""
        mt = self.tools.get("message")
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
    def _normalize_thinking_text(value: Any) -> str | None:
        """Best-effort extraction of plain thinking text from provider payloads."""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                extracted = AgentLoop._normalize_thinking_text(item)
                if extracted:
                    parts.append(extracted)
            merged = "\n".join(parts).strip()
            return merged or None
        if isinstance(value, dict):
            parts: list[str] = []
            for key in ("thinking", "reasoning_content", "text", "content", "value"):
                extracted = AgentLoop._normalize_thinking_text(value.get(key))
                if extracted:
                    parts.append(extracted)
            merged = "\n".join(parts).strip()
            return merged or None
        return None

    @classmethod
    def _extract_thinking_text(cls, response: Any) -> str | None:
        """Merge all known provider thinking fields into one displayable string."""
        merged: list[str] = []
        seen: set[str] = set()

        def _push(text: str | None) -> None:
            if not text:
                return
            clean = text.strip()
            if not clean or clean in seen:
                return
            seen.add(clean)
            merged.append(clean)

        _push(cls._strip_think(getattr(response, "content", None)))
        _push(cls._normalize_thinking_text(getattr(response, "reasoning_content", None)))
        _push(cls._normalize_thinking_text(getattr(response, "thinking_blocks", None)))
        return "\n\n".join(merged) if merged else None

    @staticmethod
    async def _stream_thinking(
        text: str,
        on_progress: Callable[..., Awaitable[None]],
        *,
        chunk_chars: int = 24,
    ) -> None:
        """Emit thinking incrementally so UI can render a streaming thought trail."""
        clean = text.strip()
        if not clean:
            return
        if len(clean) <= chunk_chars:
            await on_progress(clean)
            return

        acc = ""
        for idx in range(0, len(clean), chunk_chars):
            acc += clean[idx:idx + chunk_chars]
            await on_progress(acc)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        on_think: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages).

        on_token: if provided, called with each text chunk of the *final*
        (non-tool-call) response as it streams from the provider.  Intermediate
        tool-call turns always use the non-streaming path.

        on_think: if provided, called with each thinking/reasoning delta chunk
        during streaming so the UI can display live reasoning without mixing it
        into the main response text.

        on_tool_call_delta: if provided, called with incremental tool-call
        argument JSON strings as they stream in, so the UI can show live
        build-up of tool call arguments before the call is executed.
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        # Set to True when thinking was already streamed via on_think during a
        # _do_stream attempt that fell through to chat().  Prevents the blocking
        # chat() fallback from re-sending the same thinking via on_progress.
        _thinking_already_streamed = False

        while iteration < self.max_iterations:
            iteration += 1
            _thinking_already_streamed = False

            # ------------------------------------------------------------------
            # Streaming path: chat_stream() now accumulates tool calls internally
            # and yields them as a _TOOL_CALL_PREFIX-encoded JSON chunk at the
            # end of the stream.  This lets us handle tool calls without a second
            # LLM request.  Falls through to blocking chat() only on real errors
            # (network failures, provider-side exceptions).
            # ------------------------------------------------------------------
            _THINK_PREFIX = "\x00think\x00"
            _TOOL_CALL_PREFIX = "\x00tool_call\x00"
            _TOOL_CALL_DELTA_PREFIX = "\x00tool_call_delta\x00"

            async def _do_stream(tools_arg) -> tuple[list[str], list[ToolCallRequest] | None]:
                """Run chat_stream, routing chunks to on_token / on_think / on_tool_call_delta.

                Thinking chunks (prefixed with _THINK_PREFIX) go to on_think.
                Tool-call delta chunks (_TOOL_CALL_DELTA_PREFIX) go to on_tool_call_delta
                so the UI can show live argument build-up.
                Complete tool-call chunks (_TOOL_CALL_PREFIX) are parsed and accumulated
                — the provider yields one per complete tool call so each arrives as soon
                as it is finished rather than in a single batch at the end.
                Regular content chunks go to on_token.

                Returns (content_chunks, tool_calls).
                tool_calls is None when no tool calls were detected.
                On a hard streaming error both lists/values are empty/None and
                the caller should fall back to the blocking chat() path.
                """
                nonlocal _thinking_already_streamed
                content_chunks: list[str] = []
                tool_calls: list[ToolCallRequest] | None = None
                try:
                    async for chunk in self.provider.chat_stream(
                        messages=messages,
                        tools=tools_arg,
                        model=self.model,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        reasoning_effort=self.reasoning_effort,
                    ):
                        if chunk.startswith(_TOOL_CALL_PREFIX):
                            # Each chunk carries exactly one complete tool call.
                            # Accumulate across multiple chunks (one per call index).
                            raw = json.loads(chunk[len(_TOOL_CALL_PREFIX):])
                            if tool_calls is None:
                                tool_calls = []
                            for tc in raw:
                                try:
                                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                                except Exception:
                                    args = {}
                                tool_calls.append(ToolCallRequest(
                                    id=tc["id"],
                                    name=tc["name"],
                                    arguments=args,
                                ))
                        elif chunk.startswith(_TOOL_CALL_DELTA_PREFIX):
                            delta_json = chunk[len(_TOOL_CALL_DELTA_PREFIX):]
                            if on_tool_call_delta:
                                await on_tool_call_delta(delta_json)
                        elif chunk.startswith(_THINK_PREFIX):
                            thinking_text = chunk[len(_THINK_PREFIX):]
                            if thinking_text:
                                if on_think:
                                    await on_think(thinking_text)
                                    _thinking_already_streamed = True
                                elif on_progress:
                                    # Fallback: route thinking to on_progress
                                    # when no dedicated think callback is set.
                                    await on_progress(thinking_text)
                                    _thinking_already_streamed = True
                        else:
                            content_chunks.append(chunk)
                            if on_token:
                                await on_token(chunk)
                except Exception:
                    # Hard streaming error → signal caller to fall back to chat().
                    return [], None
                return content_chunks, tool_calls

            # Always pass the full tool definitions so the model can make multiple
            # sequential tool calls. 
            tools_arg = self.tools.get_definitions()

            if on_token is not None:
                # Always attempt streaming.  chat_stream() returns tool calls as
                # structured data so no second request is ever needed.
                chunks, stream_tool_calls = await _do_stream(tools_arg)

                if stream_tool_calls is not None:
                    # Tool calls arrived from the stream — process them directly.
                    # Any content chunks emitted before the tool calls are discarded
                    # (edge case: model prefixed tool calls with stray text).
                    if on_progress:
                        for tc in stream_tool_calls:
                            await on_progress(self._format_tool_call(tc), tool_hint=True)

                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in stream_tool_calls
                    ]
                    assistant_content = "".join(chunks) if chunks else None
                    messages = self.context.add_assistant_message(
                        messages, assistant_content, tool_call_dicts,
                    )
                    for tc in stream_tool_calls:
                        tools_used.append(tc.name)
                        args_str = json.dumps(tc.arguments, ensure_ascii=False)
                        logger.info("Tool call: {}({})", tc.name, args_str[:200])
                        result = await self.tools.execute(tc.name, tc.arguments)
                        messages = self.context.add_tool_result(
                            messages, tc.id, tc.name, result,
                        )
                    if self._message_was_sent():
                        break
                    continue  # next iteration — model will now give the final answer

                elif chunks:
                    # Streaming produced a final answer with no tool calls.
                    streamed_text = "".join(chunks)
                    clean = self._strip_think(streamed_text)
                    messages = self.context.add_assistant_message(messages, clean)
                    final_content = clean
                    break

                # Hard streaming error (empty chunks, no tool calls) →
                # fall through to the blocking chat() path below.

            response = await self.provider.chat(
                messages=messages,
                tools=tools_arg,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )

            if response.has_tool_calls:
                if on_progress:
                    # Only emit thinking from the blocking call if it was not
                    # already streamed live via on_think / on_progress above.
                    if not _thinking_already_streamed:
                        thought = self._extract_thinking_text(response)
                        if thought:
                            await self._stream_thinking(thought, on_progress)
                    for tc in response.tool_calls:
                        await on_progress(self._format_tool_call(tc), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                if self._message_was_sent():
                    break
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                if clean is None:
                    logger.warning(
                        "LLM returned empty content (finish_reason={}, content={!r}); "
                        "treating as no response",
                        response.finish_reason,
                        response.content,
                    )
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
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
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
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
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=self.memory_window)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        async def _bus_stream_token(token: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_stream_token"] = True
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=token, metadata=meta,
            ))

        async def _bus_stream_think(token: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_stream_think"] = True
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=token, metadata=meta,
            ))

        async def _bus_stream_tool_delta(delta_json: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_stream_tool_delta"] = True
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=delta_json, metadata=meta,
            ))

        # Use on_token if explicitly provided; otherwise default to _bus_stream_token
        # only for channels that support streaming (nanochat sets metadata["_streaming"]).
        _is_streaming = (msg.metadata or {}).get("_streaming")
        _effective_on_token = on_token
        if _effective_on_token is None and _is_streaming:
            _effective_on_token = _bus_stream_token
        _effective_on_think = _bus_stream_think if _is_streaming else None
        _effective_on_tool_call_delta = _bus_stream_tool_delta if _is_streaming else None

        # For streaming sessions, capture per-round thinking so it can be stored
        # as _ui_only session entries (visible to the UI, invisible to the LLM).
        # The blocking path embeds thinking in response.reasoning_content instead.
        _thinking_rounds: list[str] = []
        if _is_streaming:
            _cur_think: list[str] = []
            _thinking_active = [False]

            _base_on_think = _effective_on_think
            async def _capturing_on_think(token: str) -> None:
                _thinking_active[0] = True
                _cur_think.append(token)
                if _base_on_think:
                    await _base_on_think(token)
            _effective_on_think = _capturing_on_think

            _base_on_progress = on_progress or _bus_progress
            async def _capturing_on_progress(content: str, *, tool_hint: bool = False) -> None:
                # A tool-hint marks the end of one LLM round: flush the thinking
                # that was generated during that round.
                if tool_hint and _thinking_active[0]:
                    _thinking_rounds.append("".join(_cur_think).strip())
                    _cur_think.clear()
                    _thinking_active[0] = False
                await _base_on_progress(content, tool_hint=tool_hint)
            _effective_on_progress = _capturing_on_progress
        else:
            _effective_on_progress = on_progress or _bus_progress

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages,
            on_progress=_effective_on_progress,
            on_token=_effective_on_token,
            on_think=_effective_on_think,
            on_tool_call_delta=_effective_on_tool_call_delta,
        )

        # Flush any remaining thinking from the final round (no tool_hint fired).
        if _is_streaming and _cur_think:
            _thinking_rounds.append("".join(_cur_think).strip())

        if final_content is None:
            final_content = "I've completed processing but have no response to give."
            # Patch the fallback into all_msgs so _save_turn records a proper
            # assistant turn. Without this the session ends with a bare user
            # message; subsequent LLM calls then see two consecutive user
            # messages, which many models reject or mishandle — producing the
            # same empty-response loop on every subsequent message.
            all_msgs = list(all_msgs)
            all_msgs.append({"role": "assistant", "content": final_content})

        self._save_turn(session, all_msgs, 1 + len(history),
                        thinking_rounds=_thinking_rounds or None)
        self.sessions.save(session)

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        meta = dict(msg.metadata or {})
        meta["_raw_response"] = True
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=meta,
        )

    def _save_turn(
        self,
        session: Session,
        messages: list[dict],
        skip: int,
        thinking_rounds: list[str] | None = None,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results.

        thinking_rounds is an ordered list of thinking-text blocks, one per LLM
        call round.  Each block is inserted into the session as a ``_ui_only``
        entry immediately before the corresponding assistant message so the UI
        can replay it without ever sending it back to the LLM.
        """
        from datetime import datetime
        thinking_iter = iter(thinking_rounds or [])
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            continue  # Strip runtime context from multimodal messages
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            # Insert the thinking block that preceded this assistant message.
            # Only done for the streaming path (thinking_rounds populated by
            # _process_message); the blocking path stores thinking via
            # reasoning_content / thinking_blocks on the entry itself.
            if role == "assistant" and thinking_rounds is not None:
                think = next(thinking_iter, None)
                if think:
                    session.messages.append({
                        "role": "assistant",
                        "content": think,
                        "_ui_only": True,
                        "timestamp": datetime.now().isoformat(),
                    })
            entry.setdefault("timestamp", datetime.now().isoformat())
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
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(
            msg, session_key=session_key, on_progress=on_progress, on_token=on_token,
        )
        return response.content if response else ""
