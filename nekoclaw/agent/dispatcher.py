"""Per-session :class:`AgentLoop` dispatcher.

The dispatcher owns the shared LLM provider, workspace, and session
manager.  It listens to the :class:`~nekoclaw.bus.queue.MessageBus`'s
inbound queue and forwards every message to the per-session
:class:`~nekoclaw.agent.loop.AgentLoop` that owns it, creating new
loops (and their backing worker threads) on demand via
:meth:`AgentLoopDispatcher.get_or_create_loop`.

Splitting the dispatch responsibility out of :class:`AgentLoop` lets each
loop focus on a single conversation, and lets multiple conversations run
concurrently in their own threads/event loops.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nekoclaw.agent.loop import AgentLoop
from nekoclaw.bus.events import InboundMessage
from nekoclaw.bus.queue import MessageBus
from nekoclaw.providers.base import LLMProvider
from nekoclaw.session.manager import SessionManager


if TYPE_CHECKING:
    from nekoclaw.config.schema import Config
    from nekoclaw.cron.service import CronService


class AgentLoopDispatcher:
    """Route inbound messages to per-session :class:`AgentLoop` workers.

    The dispatcher is initialised with the *default* parameters that new
    agent loops should be constructed with (workspace, model, temperature,
    cron service, ...).  It runs on the main event loop, consumes from
    ``bus.inbound``, computes the routing session key for each message, and
    forwards the message to the corresponding loop via
    :meth:`AgentLoop.submit` (which is thread-safe).

    New :class:`AgentLoop` instances are spawned lazily by
    :meth:`get_or_create_loop` and started in their own daemon threads so
    that multiple conversations can run in parallel.
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
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
    ) -> None:
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

        self.sessions = SessionManager(workspace)
        self._loops: dict[str, AgentLoop] = {}
        self._loops_lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------------ #
    # Loop lifecycle
    # ------------------------------------------------------------------ #

    def get_or_create_loop(self, session_key: str) -> AgentLoop:
        """Return the :class:`AgentLoop` for *session_key*, creating one if
        needed.

        The newly created loop is started in its own daemon thread and the
        call blocks briefly until the worker's event loop is ready to accept
        submissions.  Thread-safe.
        """
        with self._loops_lock:
            loop = self._loops.get(session_key)
            if loop is None:
                session = self.sessions.get_or_create(session_key)
                loop = AgentLoop(
                    session=session,
                    bus=self.bus,
                    provider=self.provider,
                    workspace=self.workspace,
                    session_manager=self.sessions,
                    model=self.model,
                    max_iterations=self.max_iterations,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    memory_window=self.memory_window,
                    reasoning_effort=self.reasoning_effort,
                    cron_service=self.cron_service,
                    restrict_to_workspace=self.restrict_to_workspace,
                )
                loop.start_thread()
                self._loops[session_key] = loop
                logger.info("Spawned agent loop thread for session {}", session_key)

        loop.wait_ready(timeout=10.0)
        return loop

    def get_loop(self, session_key: str) -> AgentLoop | None:
        """Return the existing loop for *session_key* (or ``None``)."""
        with self._loops_lock:
            return self._loops.get(session_key)

    def list_loops(self) -> list[AgentLoop]:
        """Snapshot of currently-active loops."""
        with self._loops_lock:
            return list(self._loops.values())

    # ------------------------------------------------------------------ #
    # Bus consumption
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        """Consume inbound messages and forward them to per-session loops."""
        try:
            self.bus.bind_loop(asyncio.get_running_loop())
        except RuntimeError:  # pragma: no cover - defensive
            pass

        self._running = True
        logger.info("AgentLoopDispatcher started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                self._route(msg)
            except Exception:
                logger.exception("Dispatcher failed to route inbound message")

    def _route(self, msg: InboundMessage) -> None:
        """Forward *msg* to the appropriate :class:`AgentLoop`."""
        routing_key = AgentLoop._routing_session_key(msg)
        loop = self.get_or_create_loop(routing_key)
        loop.submit(msg)

    def stop(self) -> None:
        """Stop the dispatcher and signal every active loop to stop too."""
        self._running = False
        with self._loops_lock:
            loops = list(self._loops.values())
        for loop in loops:
            loop.stop()
        logger.info("AgentLoopDispatcher stopping (loops={})", len(loops))

    # ------------------------------------------------------------------ #
    # Direct invocation helpers (cron, heartbeat, CLI)
    # ------------------------------------------------------------------ #

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """Process *content* directly on the loop owning *session_key*.

        Spawns the loop if it doesn't exist yet, schedules the call on the
        loop's own event loop via :func:`asyncio.run_coroutine_threadsafe`,
        and awaits the result.
        """
        loop = self.get_or_create_loop(session_key)
        fut = loop.run_process_threadsafe(content, channel=channel, chat_id=chat_id)
        return await asyncio.wrap_future(fut)

    # ------------------------------------------------------------------ #
    # Live config propagation
    # ------------------------------------------------------------------ #

    def apply_defaults(self, new_cfg: "Config") -> None:
        """Apply updated agent defaults to the dispatcher and every loop.

        Called by :mod:`nekoclaw.config.manager` when the runtime config
        changes so that subsequently-created loops, and every currently
        active loop, see the new values.
        """
        d = new_cfg.agents.defaults
        # The stored model is qualified (``providerName/modelId``); resolve it
        # to the bare model id passed to the provider. Per-model capability
        # flags (image_input / include_reasoning) are not threaded here — they
        # are read from the active config by ``delta_to_openai`` at call time.
        model_id = new_cfg.providers.resolve(d.model).model_id

        self.model = model_id
        self.temperature = d.temperature
        self.max_tokens = d.max_tokens
        self.max_iterations = d.max_tool_iterations
        self.memory_window = d.memory_window
        self.reasoning_effort = d.reasoning_effort

        with self._loops_lock:
            loops = list(self._loops.values())

        for loop in loops:
            loop.model = model_id
            loop.temperature = d.temperature
            loop.max_tokens = d.max_tokens
            loop.max_iterations = d.max_tool_iterations
            loop.memory_window = d.memory_window
            loop.reasoning_effort = d.reasoning_effort
            sub = getattr(loop, "subagents", None)
            if sub is not None:
                sub.model = model_id
                sub.temperature = d.temperature
                sub.max_tokens = d.max_tokens
                sub.reasoning_effort = d.reasoning_effort
