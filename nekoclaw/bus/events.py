"""Event types for the message bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from nekoclaw.providers.base import StreamDelta


@dataclass
class InboundMessage:
    """Message received from a chat channel.

    ``type`` distinguishes the message's origin/intent:
      - ``"user"``       – normal request from a human user (default)
      - ``"subagent"``   – announcement produced by a spawned subagent
      - ``"user_pause"`` – pause signal from the user; causes the currently
                           running agent loop for the target session to stop
                           at the next iteration boundary. Not dispatched as
                           a regular message.
    """

    channel: str  # nekochat/telegram
    sender_id: str  # Sender identifier (e.g. platform user id, "user", "subagent")
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions
    type: Literal["user", "subagent", "user_pause"] = "user"

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``type="delta"`` carries a ``StreamDelta`` in ``msg``.
    Signal types (``stream_start``, ``stream_end``, ``clear_unsent_buffer``)
    have ``msg=None`` and are used for lifecycle management.
    """

    channel: str
    chat_id: str
    type: Literal[
        "delta",               # carries a StreamDelta in msg
        "stream_start",        # new turn starting
        "stream_end",          # turn finished; channels should flush/deliver
        "clear_unsent_buffer", # drop replay / unsent buffers
    ] = "delta"
    msg: StreamDelta | None = None
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


