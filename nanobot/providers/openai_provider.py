"""OpenAI provider implementation using the official OpenAI Python SDK."""

import json
import secrets
import string
from typing import Any, AsyncIterator

import json_repair
from loguru import logger
from openai import AsyncOpenAI

from nanobot.providers.base import LLMProvider, StreamDelta, ToolCallRequest, build_stream_deltas

_ALLOWED_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})
_ALNUM = string.ascii_letters + string.digits


def _short_tool_id() -> str:
    """Generate a 9-char alphanumeric ID for tool calls."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


class OpenAIProvider(LLMProvider):
    """LLM provider using the official OpenAI Python SDK.

    Supports any OpenAI-compatible endpoint via ``api_base`` / ``base_url``.
    The message and streaming interface (``StreamDelta``, tool calls, reasoning)
    is identical to the former LiteLLMProvider.
    """

    def __init__(
        self,
        default_model: str,
        api_key: str | None = None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._client = AsyncOpenAI(
            api_key=api_key or "no-key",
            base_url=api_base or None,
            default_headers=extra_headers or {},
        )

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip non-standard keys from messages before sending to the API."""
        return LLMProvider._sanitize_request_messages(messages, _ALLOWED_MSG_KEYS)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> list[StreamDelta]:
        """Send a chat completion request via the OpenAI SDK.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI function-call format.
            model: Model identifier (e.g. 'gpt-4o').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            reasoning_effort: 'low' / 'medium' / 'high' for o-series reasoning models.

        Returns:
            Complete response as a list of ``StreamDelta`` objects.
        """
        model = model or self.default_model
        max_tokens = max(1, max_tokens)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await self._client.chat.completions.create(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            return [StreamDelta(type="content", content=f"Error calling LLM: {str(e)}")]

    def _parse_response(self, response: Any) -> list[StreamDelta]:
        """Parse an OpenAI completion response into stream-style deltas."""
        choice = response.choices[0]
        message = choice.message
        content = message.content

        tool_calls: list[ToolCallRequest] = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json_repair.loads(args)
                tool_calls.append(ToolCallRequest(
                    id=_short_tool_id(),
                    name=tc.function.name,
                    arguments=args,
                ))

        reasoning_content = getattr(message, "reasoning_content", None) or None

        return build_stream_deltas(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Stream structured response deltas via the OpenAI SDK.

        Yields ``StreamDelta`` objects as they arrive. Tool-call deltas are
        accumulated and emitted with a ``partial`` flag during streaming, then
        finalized at the end of the stream (matching the former LiteLLM behaviour).
        """
        model = model or self.default_model
        max_tokens = max(1, max_tokens)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # index → {"id": str, "name": str, "arguments": str}
        accumulated_tool_calls: dict[int, dict[str, str]] = {}
        finalized_indices: set[int] = set()

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                if getattr(delta, "tool_calls", None):
                    for tc_delta in delta.tool_calls:
                        idx = getattr(tc_delta, "index", 0) or 0

                        # When a higher index appears, all lower indices are complete.
                        for prev_idx in sorted(accumulated_tool_calls.keys()):
                            if prev_idx < idx and prev_idx not in finalized_indices:
                                yield StreamDelta(
                                    type="tool_call",
                                    content=json.dumps(accumulated_tool_calls[prev_idx]),
                                )
                                finalized_indices.add(prev_idx)

                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}

                        tc = accumulated_tool_calls[idx]
                        if getattr(tc_delta, "id", None):
                            tc["id"] = tc_delta.id
                        fn = getattr(tc_delta, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                tc["name"] += fn.name
                            if getattr(fn, "arguments", None):
                                tc["arguments"] += fn.arguments

                        if tc["name"] or tc["arguments"]:
                            yield StreamDelta(
                                type="tool_call",
                                content=json.dumps({
                                    "index": idx,
                                    "id": tc["id"],
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                    "partial": True,
                                }),
                            )

                # Reasoning tokens from o-series models
                reasoning_text = getattr(delta, "reasoning_content", None)
                if reasoning_text:
                    yield StreamDelta(type="thinking", content=reasoning_text)

                text = getattr(delta, "content", None)
                if text:
                    yield StreamDelta(type="content", content=text)

            # Finalize any tool calls not yet yielded (last index or sole call).
            for idx in sorted(accumulated_tool_calls.keys()):
                if idx not in finalized_indices:
                    yield StreamDelta(
                        type="tool_call",
                        content=json.dumps(accumulated_tool_calls[idx]),
                    )

        except Exception as e:
            logger.warning("Streaming failed, no tokens yielded: {}", e)
            return

    def get_default_model(self) -> str:
        """Return the default model identifier."""
        return self.default_model
