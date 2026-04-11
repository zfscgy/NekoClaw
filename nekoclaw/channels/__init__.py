"""Chat channels module with plugin architecture."""

from nekoclaw.channels.base import BaseChannel
from nekoclaw.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
