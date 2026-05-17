"""Agent loop: the core processing engine for a single conversation session."""

from __future__ import annotations

import asyncio
import json
import threading
import traceback
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
from nekoclaw.agent.tools.spawn import CallSubagentTool
from nekoclaw.agent.tools.web import WebFetchTool, WebSearchTool
from nekoclaw.bus.events import InboundMessage, OutboundMessage
from nekoclaw.bus.queue import MessageBus
from nekoclaw.providers.base import LLMProvider, StreamDelta, ToolCallRequest, ToolCallResult, delta_to_openai, is_error_content
from nekoclaw.session.manager import Session, SessionManager


if TYPE_CHECKING:
    from nekoclaw.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine for a *single conversation
    session*.

    Each instance:

    1. Receives messages from its own thread-local inbox (populated by
       :class:`~nekoclaw.agent.dispatcher.AgentLoopDispatcher`).
    2. Builds context with history, memory, skills.
    3. Calls the LLM.
    4. Executes tool calls.
    5. Sends responses back to the shared message bus.

    The loop is designed to run inside its own thread with its own
    ``asyncio`` event loop so that multiple conversations can execute in
    parallel.  The shared :class:`~nekoclaw.bus.queue.MessageBus` is
    thread-aware and forwards outbound puts to its owner loop.
    """

    def __init__(
        self,
        session: Session,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        session_manager: SessionManager | None = None,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
    ):
        self.session = session
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        # SessionManager is shared across all loops (owned by the dispatcher)
        # so saves/load reuse the same on-disk cache.  Fall back to a
        # private manager when no shared one is provided (mostly useful for
        # tests / direct construction).
        self._session_manager = session_manager or SessionManager(workspace)
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self.context = ContextBuilder(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            sessions=self._session_manager,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        # Loop-local async primitives — created when the worker thread starts
        # its event loop in ``_async_main``.  They are ``None`` until then.
        self._processing_lock: asyncio.Lock | None = None
        self._consolidation_lock: asyncio.Lock | None = None
        self._inbox: asyncio.Queue[InboundMessage] | None = None
        self._inflight_inbox: asyncio.Queue[InboundMessage] | None = None
        self._consolidating: bool = False
        self._consolidation_tasks: set[asyncio.Task] = set()
        # ``True`` while the user has asked the currently-running iteration to
        # stop at the next iteration boundary.  Cleared by the loop itself.
        self._pause_requested: bool = False

        # Cross-thread plumbing
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._pending_inbox: list[InboundMessage] = []
        self._pending_inbox_lock = threading.Lock()

        self._register_default_tools()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start_thread(self) -> threading.Thread:
        """Start this agent loop in a new daemon thread.

        The thread spins up its own ``asyncio`` event loop and runs
        :meth:`run` until :meth:`stop` is called.  Returns the worker thread.
        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._thread is not None and self._thread.is_alive():
            return self._thread

        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"AgentLoop-{self.session.key}",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def wait_ready(self, timeout: float | None = 10.0) -> bool:
        """Block until the worker thread has bound its event loop.

        Returns ``True`` once the loop is accepting submissions, or
        ``False`` on timeout.
        """
        return self._ready_event.wait(timeout=timeout)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception:
            logger.exception("AgentLoop thread crashed for session {}", self.session.key)
        finally:
            self._ready_event.set()  # unblock any laggards waiting on ready

    async def _async_main(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        self._inbox = asyncio.Queue()
        self._processing_lock = asyncio.Lock()
        self._consolidation_lock = asyncio.Lock()

        # Drain anything that arrived before the event loop was ready.
        with self._pending_inbox_lock:
            for m in self._pending_inbox:
                self._inbox.put_nowait(m)
            self._pending_inbox.clear()

        self._ready_event.set()
        await self.run()

    def submit(self, msg: InboundMessage) -> None:
        """Submit a message to this loop's inbox.

        Thread-safe: may be called from any thread.  If the worker has not
        started yet, the message is buffered and replayed when the loop's
        event loop comes up.
        """
        loop = self._event_loop
        inbox = self._inbox
        if loop is None or inbox is None or not loop.is_running():
            with self._pending_inbox_lock:
                self._pending_inbox.append(msg)
            return

        loop.call_soon_threadsafe(inbox.put_nowait, msg)

    # ------------------------------------------------------------------ #
    # Tools / context
    # ------------------------------------------------------------------ #

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(working_dir=str(self.workspace)))
        self.tools.register(WebSearchTool(max_results=10))
        self.tools.register(WebFetchTool())
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(CallSubagentTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    @staticmethod
    def _routing_session_key(msg: InboundMessage) -> str:
        """Return the parent conversation key used for dispatch/in-flight routing."""
        if msg.session_key_override:
            return msg.session_key_override
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            return f"{channel}:{chat_id}"
        return msg.session_key

    @staticmethod
    def _subagent_ref_delta(msg: InboundMessage) -> StreamDelta | None:
        """Convert a subagent completion announcement into a persisted delta."""
        meta = msg.metadata
        session_id = meta.get("subagent_session_id")
        if not session_id:
            return None
        return StreamDelta(
            type="subagent_ref",
            content={
                "session_id": session_id,
                "label": meta.get("subagent_label", ""),
                "status": meta.get("subagent_status", "ok"),
                "task": meta.get("subagent_task", ""),
                "announce": msg.content,
            },
            time=datetime.now(timezone.utc).isoformat(),
        )

    def _drain_inbox_into_messages(
        self,
        inbox: asyncio.Queue[InboundMessage],
        session: Session,
        messages: list[StreamDelta],
    ) -> list[StreamDelta]:
        """Drain any queued in-flight messages for this session and append them
        as ``user`` turns to *messages*. Returns the injected deltas.

        Whenever at least one drained message is a subagent completion
        announcement, an additional transient ``system`` delta summarising
        finished/running subagents for this session is appended so the main
        agent stays aware of overall subagent state.
        """
        injected: list[StreamDelta] = []
        finished_subagents: list[tuple[str, str]] = []  # (task_id, label)
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
            delta = self._subagent_ref_delta(pending) if pending.type == "subagent" else None
            if delta is None:
                delta = StreamDelta(
                    type="user",
                    content=pending.content,
                    media=media_paths or [],
                    time=datetime.now(timezone.utc).isoformat(),
                )
            else:
                meta = pending.metadata
                ref_session = meta.get("subagent_session_id", "")
                task_id = ref_session.split(":", 1)[1] if ":" in ref_session else ref_session
                label = meta.get("subagent_label", "") or task_id or "subagent"
                finished_subagents.append((task_id, label))
            messages.append(delta)
            injected.append(delta)

        if finished_subagents:
            status_delta = self._build_subagent_status_delta(session, finished_subagents)
            messages.append(status_delta)
            injected.append(status_delta)

        return injected

    def _build_subagent_status_delta(
        self,
        session: Session,
        finished_now: list[tuple[str, str]],
    ) -> StreamDelta:
        """Build a transient ``system`` delta summarising subagent state.

        ``finished_now`` lists ``(task_id, label)`` for subagents whose
        announcements were drained in this batch and haven't been persisted
        yet. The full "Finished" list is built from persisted ``subagent_ref``
        deltas on ``session.messages`` plus this batch, so the main agent sees
        every subagent that has finished in this session. Currently-running
        subagents are queried from the manager.

        The delta is typed ``system`` so it informs the next LLM iteration
        without being persisted to ``session.messages``.
        """
        seen_ids: set[str] = set()
        finished: list[tuple[str, str]] = []

        for m in session.messages:
            if m.type != "subagent_ref" or not isinstance(m.content, dict):
                continue
            sid = m.content.get("session_id", "") or ""
            task_id = sid.split(":", 1)[1] if ":" in sid else sid
            if not task_id or task_id in seen_ids:
                continue
            seen_ids.add(task_id)
            label = m.content.get("label", "") or task_id
            finished.append((task_id, label))

        for task_id, label in finished_now:
            if not task_id or task_id in seen_ids:
                continue
            seen_ids.add(task_id)
            finished.append((task_id, label))

        running = self.subagents.get_session_running(session.key)

        def _fmt(items: list[tuple[str, str]]) -> str:
            return ", ".join(f"'{label}' ({tid})" for tid, label in items) if items else "(none)"

        lines = [
            f"Subagents status: {len(finished)} finished, {len(running)} running.",
            f"Finished: {_fmt(finished)}",
            f"Running: {_fmt(running)}",
        ]
        return StreamDelta(type="system", content="\n".join(lines))

    def _release_inflight_inbox(self) -> None:
        """Drop the in-flight inbox and re-queue any leftover messages onto
        the loop's main inbox so a follow-up dispatch can pick them up.
        """
        inbox = self._inflight_inbox
        self._inflight_inbox = None
        if inbox is None or self._inbox is None:
            return
        while not inbox.empty():
            try:
                pending = inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            logger.info(
                "Re-queuing unconsumed in-flight message for session {}",
                self.session.key,
            )
            self._inbox.put_nowait(pending)

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
                dest = self._session_manager.save_media(session_key, src)
                saved.append(str(dest))
            except Exception:
                logger.warning("Failed to save media file {}, using original path", p)
                saved.append(str(src))
        return saved or None

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("send_message_with_attachments", "call_subagent", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "send_message_with_attachments" else []))

    # ------------------------------------------------------------------ #
    # Core processing
    # ------------------------------------------------------------------ #

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
        inbox = self._inflight_inbox

        while iteration < self.max_iterations:
            if self._pause_requested:
                self._pause_requested = False
                logger.info("Agent loop paused by user for session {}", session.key)
                if final_content is None:
                    final_content = "Paused by user."
                break

            if inbox is not None:
                injected = self._drain_inbox_into_messages(inbox, session, messages)
                if injected:
                    self._save_session(session)
                    for delta in injected:
                        if delta.type == "subagent_ref":
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=channel,
                                chat_id=chat_id,
                                type="delta",
                                msg=delta,
                            ))

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

                if tool_results:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    messages.append(StreamDelta(
                        type="tool_call_results", content=tool_results, time=now_iso,
                    ))

                    await self.bus.publish_outbound(OutboundMessage(
                        channel=channel, chat_id=chat_id,
                        type="delta",
                        msg=StreamDelta(
                            type="tool_call_results", content=tool_results, time=now_iso,
                        ),
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
                if response_content is None:
                    logger.warning(
                        "LLM returned empty content (content={!r}); "
                        "treating as no response",
                        response_content,
                    )
                if response_content:
                    if is_error_content(response_content):
                        logger.error("LLM returned error: {}", response_content[:200])
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
        """Consume the per-loop inbox and dispatch each message as a task.

        The loop pulls messages submitted via :meth:`submit` (typically by
        :class:`~nekoclaw.agent.dispatcher.AgentLoopDispatcher`) until
        :meth:`stop` is called.
        """
        assert self._inbox is not None, "AgentLoop.run() called outside its worker thread"
        self._running = True
        logger.info("Agent loop started for session {}", self.session.key)

        while self._running:
            try:
                msg = await asyncio.wait_for(self._inbox.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.type == "user_pause":
                # Flag the currently-running ``_run_agent_loop`` to exit at
                # its next iteration boundary.  Does not start a new task.
                self._pause_requested = True
                logger.info("Pause requested for session {}", self.session.key)
                continue

            # If an agent loop is already running, route regular user
            # messages and subagent announcements to its in-flight inbox so
            # they can be picked up at the next iteration boundary instead of
            # queuing behind ``_processing_lock``.
            if msg.type in {"user", "subagent"} and self._inflight_inbox is not None:
                await self._inflight_inbox.put(msg)
                logger.info(
                    "Routed in-flight {} message to running loop for session {}",
                    msg.type, self.session.key,
                )
                continue

            asyncio.create_task(self._dispatch(msg))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the per-session processing lock."""
        assert self._processing_lock is not None
        async with self._processing_lock:
            try:
                await self._process_message(msg)
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", self.session.key)
                raise
            except Exception:
                tb_lines = traceback.format_exc().splitlines()
                short_tb = "\n".join(tb_lines[-15:])
                logger.exception("Error processing message for session {}", self.session.key)
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
        logger.info("Agent loop stopping for session {}", self.session.key)

    async def _process_message(self, msg: InboundMessage) -> str:
        """Process a single inbound message. Returns the final text content."""
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
        else:
            preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
            logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)
            channel, chat_id = msg.channel, msg.chat_id

        session = self.session

        initial_subagent_ref = self._subagent_ref_delta(msg) if msg.type == "subagent" else None
        emit_subagent_ref = False
        if initial_subagent_ref is not None and isinstance(initial_subagent_ref.content, dict):
            session_id = initial_subagent_ref.content.get("session_id")
            ref_exists = any(
                m.type == "subagent_ref"
                and isinstance(m.content, dict)
                and m.content.get("session_id") == session_id
                for m in session.messages
            )
            emit_subagent_ref = not ref_exists

        if msg.channel != "system":
            unconsolidated = len(session.messages) - session.last_consolidated
            if unconsolidated >= self.memory_window and not self._consolidating:
                self._consolidating = True
                lock = self._consolidation_lock

                async def _consolidate_and_unlock():
                    try:
                        if lock is not None:
                            async with lock:
                                await self._consolidate_memory(session)
                        else:
                            await self._consolidate_memory(session)
                    finally:
                        self._consolidating = False
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

        media_paths = self._save_inbound_media(session.key, msg.media)

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

        # Persist the subagent_ref to JSONL *before* broadcasting the delta.
        # The channel clears its in-memory subagent buffer when the delta is
        # broadcast, so the JSONL must already contain the ref to survive a
        # client reload. We do this after ``build_messages`` so that the LLM
        # context for this turn isn't built from a session that already has
        # the announce content as a ``subagent_ref`` (which ``_process_history``
        # would re-emit as a user message and double-count the announce).
        # ``_save_session``/``_pending_subagent_ref`` deduplicate by
        # session_id, so the user delta in ``initial_messages`` will be
        # dropped on the next flush instead of being persisted alongside.
        if emit_subagent_ref and initial_subagent_ref is not None:
            session.messages.append(initial_subagent_ref)
            self._session_manager.save(session)
            await self.bus.publish_outbound(OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                type="delta",
                msg=initial_subagent_ref,
            ))

        # Register an in-flight inbox so follow-up user messages arriving
        # while the agent loop is running can be injected at the next
        # iteration boundary instead of queuing behind ``_processing_lock``.
        self._inflight_inbox = asyncio.Queue()
        try:
            final_content, tools_used = await self._run_agent_loop(
                channel=channel,
                chat_id=chat_id,
                session=session,
            )
        finally:
            self._release_inflight_inbox()

        if final_content is None:
            # If the agent already replied via the message tool, the response
            # was delivered out-of-band and no fallback content is needed.
            if "send_message_with_attachments" not in tools_used:
                final_content = "Background task completed." if msg.channel == "system" else ""

        await self.bus.publish_outbound(OutboundMessage(
            channel=channel, chat_id=chat_id, type="stream_end",
        ))
        return final_content

    def _save_session(self, session: Session) -> None:
        """Append new StreamDeltas from session.initial_messages to the session and persist to disk."""
        # Check for pending subagent reference (set by _process_message for subagent announcements)
        sub_ref: dict | None = getattr(session, "_pending_subagent_ref", None)
        sub_ref_emitted = False
        existing_subagent_refs = {
            m.content.get("session_id")
            for m in session.messages
            if m.type == "subagent_ref" and isinstance(m.content, dict)
        }

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
                    session_id = sub_ref["subagent_session_id"]
                    if session_id not in existing_subagent_refs:
                        session.messages.append(StreamDelta(
                            type="subagent_ref",
                            content={
                                "session_id": session_id,
                                "label": sub_ref.get("subagent_label", ""),
                                "status": sub_ref.get("subagent_status", "ok"),
                                "task": sub_ref.get("subagent_task", ""),
                                "announce": raw,
                            },
                            time=delta.time or datetime.now(timezone.utc).isoformat(),
                        ))
                        existing_subagent_refs.add(session_id)
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
                session.messages.append(StreamDelta(
                    type="user",
                    content=content,
                    media=delta.media,
                    time=delta.time or datetime.now(timezone.utc).isoformat(),
                ))
                continue

            if delta.type == "subagent_ref" and isinstance(delta.content, dict):
                session_id = delta.content.get("session_id")
                if session_id in existing_subagent_refs:
                    continue
                existing_subagent_refs.add(session_id)

            session.messages.append(delta)

        session._save_offset = len(messages)
        session.updated_at = datetime.now()
        session._pending_subagent_ref = None  # type: ignore[attr-defined]
        self._session_manager.save(session)

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(self.workspace).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """Process a message directly on this loop (for CLI/cron/heartbeat usage).

        Must be called from the loop's own event loop.  Cross-thread callers
        should use :meth:`run_process_threadsafe` (or the dispatcher's
        :meth:`AgentLoopDispatcher.process_direct`).
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            session_key_override=self.session.key,
        )
        return await self._process_message(msg)

    def run_process_threadsafe(
        self,
        content: str,
        channel: str = "cli",
        chat_id: str = "direct",
        timeout: float | None = None,
    ) -> "asyncio.futures.Future[str]":
        """Schedule a direct process call onto this loop's event loop from
        another thread.  Returns a ``concurrent.futures.Future`` so callers
        can ``await asyncio.wrap_future(...)`` from their own loop or
        ``.result()`` synchronously.
        """
        if self._event_loop is None or not self._event_loop.is_running():
            raise RuntimeError(
                f"AgentLoop for session {self.session.key!r} is not running"
            )
        coro = self.process_direct(content, channel=channel, chat_id=chat_id)
        return asyncio.run_coroutine_threadsafe(coro, self._event_loop)
