"""Base channel interface for chat platforms."""

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from nekoclaw.bus.events import InboundMessage, OutboundMessage
from nekoclaw.bus.queue import MessageBus
from nekoclaw.providers.base import StreamDelta


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the nekoclaw message bus.

    The default ``send()`` buffers ``delta`` messages and assembles them into
    a single ``deliver()`` call when ``stream_end`` arrives.  Channels that
    need real-time streaming (e.g. nekochat) should override ``send()``.
    """

    name: str = "base"

    def __init__(self, config: Any, bus: MessageBus):
        self.config = config
        self.bus = bus
        self._running = False
        self._outbound_buffers: dict[str, list[str]] = {}

    @abstractmethod
    async def start(self) -> None:
        """Start the channel and begin listening for messages."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    async def send(self, msg: OutboundMessage) -> None:
        """Handle an outbound bus message.

        Buffers content deltas and calls ``deliver()`` with an assembled
        message when ``stream_end`` is received.  Override in subclasses
        that need per-token streaming or custom signal handling.
        """
        if msg.type == "delta" and msg.msg is not None:
            if msg.msg.type == "content" and isinstance(msg.msg.content, str):
                self._outbound_buffers.setdefault(msg.chat_id, []).append(msg.msg.content)
            return

        if msg.type == "stream_end":
            parts = self._outbound_buffers.pop(msg.chat_id, [])
            content = "".join(parts)
            if content:
                assembled = OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    msg=StreamDelta(type="content", content=content),
                    reply_to=msg.reply_to, media=msg.media, metadata=msg.metadata,
                )
                await self.deliver(assembled)
            return

        if msg.type == "clear_unsent_buffer":
            self._outbound_buffers.pop(msg.chat_id, [])
            return

    @abstractmethod
    async def deliver(self, msg: OutboundMessage) -> None:
        """Deliver an assembled message through this channel.

        Called by the default ``send()`` after content deltas have been
        buffered and assembled.  ``msg.msg`` is a ``StreamDelta`` with the
        complete content.
        """
        pass

    def is_allowed(self, sender_id: str) -> bool:
        """Check if *sender_id* is permitted.  Empty list → deny all; ``"*"`` → allow all."""
        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list:
            logger.warning("{}: allow_from is empty — all access denied", self.name)
            return False
        if "*" in allow_list:
            return True
        return str(sender_id) in allow_list

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """
        Handle an incoming message from the chat platform.

        This method checks permissions and forwards to the bus.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
            session_key: Optional session key override (e.g. thread-scoped sessions).
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id, self.name,
            )
            return

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
            session_key_override=session_key,
        )

        await self.bus.publish_inbound(msg)

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
