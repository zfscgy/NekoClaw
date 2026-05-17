# AutoCython No Compile
"""Async message queue for decoupled channel-agent communication."""

from __future__ import annotations

import asyncio
from typing import Any

from nekoclaw.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    The bus is *thread-aware*: the underlying ``asyncio.Queue`` instances
    are owned by a single "bus loop" (typically the main asyncio loop that
    hosts the channel manager and the agent dispatcher).  Producers running
    on a different event loop / thread (e.g. per-conversation agent loops
    running in their own threads) call :meth:`publish_inbound` /
    :meth:`publish_outbound`; the publish methods automatically schedule the
    put onto the owner loop via :func:`asyncio.run_coroutine_threadsafe` and
    await the resulting future, so the queues themselves are only ever
    touched from their owner loop.
    """

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._owner_loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the bus to a specific event loop.

        Calls to :meth:`publish_inbound` / :meth:`publish_outbound` from any
        other event loop (e.g. an agent loop running in a worker thread)
        will be forwarded to *loop* via :func:`asyncio.run_coroutine_threadsafe`.
        """
        self._owner_loop = loop

    async def _publish(self, queue: asyncio.Queue[Any], msg: Any) -> None:
        """Put *msg* onto *queue*, hopping to the owner loop if needed."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        owner = self._owner_loop
        if owner is None:
            # Lazily bind to the first loop that touches the bus.
            self._owner_loop = running
            owner = running

        if running is owner or owner is None:
            await queue.put(msg)
            return

        fut = asyncio.run_coroutine_threadsafe(queue.put(msg), owner)
        await asyncio.wrap_future(fut)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self._publish(self.inbound, msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self._publish(self.outbound, msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
