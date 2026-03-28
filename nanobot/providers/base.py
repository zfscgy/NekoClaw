"""Base LLM provider interface."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal

import json_repair


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    
    Implementations should handle the specifics of each provider's API
    while maintaining a consistent interface.
    """

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base

    @staticmethod
    def _sanitize_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace empty text content that causes provider 400 errors.

        Empty content can appear when MCP tools return nothing. Most providers
        reject empty-string content or empty text blocks in list content.
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")

            if isinstance(content, str) and not content:
                clean = dict(msg)
                clean["content"] = None if (msg.get("role") == "assistant" and msg.get("tool_calls")) else "(empty)"
                result.append(clean)
                continue

            if isinstance(content, list):
                filtered = [
                    item for item in content
                    if not (
                        isinstance(item, dict)
                        and item.get("type") in ("text", "input_text", "output_text")
                        and not item.get("text")
                    )
                ]
                if len(filtered) != len(content):
                    clean = dict(msg)
                    if filtered:
                        clean["content"] = filtered
                    elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                        clean["content"] = None
                    else:
                        clean["content"] = "(empty)"
                    result.append(clean)
                    continue

            if isinstance(content, dict):
                clean = dict(msg)
                clean["content"] = [content]
                result.append(clean)
                continue

            result.append(msg)
        return result

    @staticmethod
    def _sanitize_request_messages(
        messages: list[dict[str, Any]],
        allowed_keys: frozenset[str],
    ) -> list[dict[str, Any]]:
        """Keep only provider-safe message keys and normalize assistant content."""
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in allowed_keys}
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> list["StreamDelta"]:
        """
        Send a chat completion request.
        
        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier (provider-specific).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
        
        Returns:
            Complete response as a list of ``StreamDelta`` objects.
        """
        pass

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator["StreamDelta"]:
        """Stream chat completion deltas as they arrive.

        Yields ``StreamDelta`` objects one chunk at a time. The default
        implementation falls back to a single ``chat()`` call so providers
        that do not override this still work — they just won't stream.
        Providers that support native streaming should override this method.
        """
        deltas = await self.chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
        for delta in deltas:
            yield delta
        return

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass


@dataclass
class StreamDelta:
    """A delta from the LLM stream."""
    type: Literal["tool_call", "thinking", "content"]
    content: str  # tool_call: json string for one call, thinking: text, content: text


def normalize_thinking_text(value: Any) -> str | None:
    """Flatten provider-specific thinking payloads into displayable text."""
    if value is None:
        return None
    if isinstance(value, str):
        clean = value.strip()
        return clean or None
    if isinstance(value, list):
        parts: list[str] = []
        seen: set[str] = set()
        for item in value:
            extracted = normalize_thinking_text(item)
            if extracted and extracted not in seen:
                seen.add(extracted)
                parts.append(extracted)
        merged = "\n".join(parts).strip()
        return merged or None
    if isinstance(value, dict):
        parts: list[str] = []
        seen: set[str] = set()
        for key in ("thinking", "reasoning_content", "text", "content", "value", "thought"):
            extracted = normalize_thinking_text(value.get(key))
            if extracted and extracted not in seen:
                seen.add(extracted)
                parts.append(extracted)
        merged = "\n".join(parts).strip()
        return merged or None
    return None


def build_stream_deltas(
    *,
    content: str | None,
    tool_calls: list[ToolCallRequest] | None = None,
    reasoning_content: str | None = None,
    thinking_blocks: list[dict[str, Any]] | None = None,
) -> list[StreamDelta]:
    """Build complete deltas for a blocking chat response."""
    deltas: list[StreamDelta] = []

    thinking_parts: list[str] = []
    seen: set[str] = set()
    for source in (reasoning_content, thinking_blocks):
        extracted = normalize_thinking_text(source)
        if extracted and extracted not in seen:
            seen.add(extracted)
            thinking_parts.append(extracted)
    if thinking_parts:
        deltas.append(StreamDelta(type="thinking", content="\n".join(thinking_parts)))

    for tc in tool_calls or []:
        raw_arguments = tc.arguments
        if isinstance(raw_arguments, str):
            arguments = raw_arguments
        else:
            arguments = json.dumps(raw_arguments, ensure_ascii=False)
        deltas.append(
            StreamDelta(
                type="tool_call",
                content=json.dumps(
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": arguments,
                    },
                    ensure_ascii=False,
                ),
            )
        )

    if content:
        deltas.append(StreamDelta(type="content", content=content))

    return deltas


def parse_stream_deltas(
    deltas: list[StreamDelta],
) -> tuple[str | None, list[ToolCallRequest], str | None]:
    """Extract complete content, tool calls, and thinking from deltas."""
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []

    for delta in deltas:
        if delta.type == "content":
            content_parts.append(delta.content)
            continue
        if delta.type == "thinking":
            if delta.content:
                thinking_parts.append(delta.content)
            continue
        if delta.type != "tool_call":
            continue

        try:
            payload = json.loads(delta.content)
        except Exception:
            continue

        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict) or item.get("partial"):
                continue
            args = item.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json_repair.loads(args)
                except Exception:
                    args = {}
            tool_calls.append(
                ToolCallRequest(
                    id=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    arguments=args,
                )
            )

    content = "".join(content_parts) if content_parts else None
    thinking = "\n".join(part for part in thinking_parts if part).strip() or None
    return content, tool_calls, thinking


def is_error_content(text: str | None) -> bool:
    """Best-effort detection for provider error strings returned as content."""
    if not text:
        return False
    return text.startswith(
        (
            "Error calling ",
            "Error parsing ",
            "Azure OpenAI API Error ",
            "Error: ",
            "HTTP ",
        )
    )
