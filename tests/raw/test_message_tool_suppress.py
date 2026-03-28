"""Test message tool suppress logic for final replies."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import StreamDelta, ToolCallRequest, build_stream_deltas


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model", memory_window=10)


class TestMessageToolSuppressLogic:
    """Final reply suppressed only when message tool sends to the same target."""

    @pytest.mark.asyncio
    async def test_suppress_when_sent_to_same_target(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(
            id="call1", name="message",
            arguments={"content": "Hello", "channel": "feishu", "chat_id": "chat123"},
        )
        calls = iter([
            build_stream_deltas(content="", tool_calls=[tool_call]),
            [StreamDelta(type="content", content="Done")],
        ])
        loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        msg = InboundMessage(channel="feishu", sender_id="user1", chat_id="chat123", content="Send")
        result = await loop._process_message(msg)

        assert len(sent) == 1
        assert result is None  # suppressed

    @pytest.mark.asyncio
    async def test_not_suppress_when_sent_to_different_target(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(
            id="call1", name="message",
            arguments={"content": "Email content", "channel": "email", "chat_id": "user@example.com"},
        )
        calls = iter([
            build_stream_deltas(content="", tool_calls=[tool_call]),
            [StreamDelta(type="content", content="I've sent the email.")],
        ])
        loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        msg = InboundMessage(channel="feishu", sender_id="user1", chat_id="chat123", content="Send email")
        result = await loop._process_message(msg)

        assert len(sent) == 1
        assert sent[0].channel == "email"
        assert result is not None  # not suppressed
        assert result.channel == "feishu"

    @pytest.mark.asyncio
    async def test_not_suppress_when_no_message_tool_used(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=[StreamDelta(type="content", content="Hello!")])
        loop.tools.get_definitions = MagicMock(return_value=[])

        msg = InboundMessage(channel="feishu", sender_id="user1", chat_id="chat123", content="Hi")
        result = await loop._process_message(msg)

        assert result is not None
        assert "Hello" in result.content

    async def test_progress_hides_internal_reasoning(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(id="call1", name="read_file", arguments={"path": "foo.txt"})
        calls = iter([
            build_stream_deltas(
                content="Visible<think>hidden</think>",
                tool_calls=[tool_call],
                reasoning_content="secret reasoning",
                thinking_blocks=[{"signature": "sig", "thought": "secret thought"}],
            ),
            [StreamDelta(type="content", content="Done")],
        ])
        loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.tools.execute = AsyncMock(return_value="ok")

        deltas: list[StreamDelta] = []

        async def on_delta(delta: StreamDelta) -> None:
            deltas.append(delta)

        final_content, _, _ = await loop._run_agent_loop([], on_delta=on_delta)

        assert final_content == "Done"
        assert [delta.type for delta in deltas] == ["thinking", "tool_call", "content", "content"]
        assert deltas[0].content == "secret reasoning\nsecret thought"
        assert deltas[2].content == "Visible<think>hidden</think>"
        assert deltas[3].content == "Done"

    @pytest.mark.asyncio
    async def test_streaming_accumulates_partial_tool_call_json_until_complete(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.tools.execute = AsyncMock(return_value="file contents")

        async def first_stream():
            yield StreamDelta(type="tool_call", content='{"id":"call1","name":"read_file","arguments":"{')
            yield StreamDelta(type="tool_call", content='\\"path\\":\\"foo.txt\\"}"}')

        async def empty_stream():
            if False:
                yield

        stream_calls = iter([first_stream(), empty_stream()])
        loop.provider.chat_stream = MagicMock(side_effect=lambda *a, **kw: next(stream_calls))
        loop.provider.chat = AsyncMock(return_value=[StreamDelta(type="content", content="Done")])

        deltas: list[StreamDelta] = []

        async def on_delta(delta: StreamDelta) -> None:
            deltas.append(delta)

        final_content, _, _ = await loop._run_agent_loop([], on_delta=on_delta)

        assert final_content == "Done"
        loop.tools.execute.assert_awaited_once_with("read_file", {"path": "foo.txt"})
        assert [delta.type for delta in deltas] == ["tool_call", "tool_call", "content"]

    @pytest.mark.asyncio
    async def test_blocking_path_ignores_incomplete_tool_call_delta(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])
        loop.tools.execute = AsyncMock(return_value="unused")
        loop.provider.chat = AsyncMock(
            return_value=[
                StreamDelta(type="tool_call", content='{"id":"call1","name":"read_file","arguments":"{'),
                StreamDelta(type="content", content="Done"),
            ]
        )

        final_content, _, _ = await loop._run_agent_loop([])

        assert final_content == "Done"
        loop.tools.execute.assert_not_called()


class TestMessageToolTurnTracking:

    def test_sent_in_turn_tracks_same_target(self) -> None:
        tool = MessageTool()
        tool.set_context("feishu", "chat1")
        assert not tool._sent_in_turn
        tool._sent_in_turn = True
        assert tool._sent_in_turn

    def test_start_turn_resets(self) -> None:
        tool = MessageTool()
        tool._sent_in_turn = True
        tool.start_turn()
        assert not tool._sent_in_turn
