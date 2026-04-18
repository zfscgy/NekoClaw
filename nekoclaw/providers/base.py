"""Base LLM provider interface."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""
    index: int
    id: str
    name: str
    arguments: dict[str, Any] | str
    partial: bool = False


@dataclass
class ToolCallResult:
    """Result from executing a tool call."""
    tool_call_id: str
    name: str
    content: str


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
    r"""A delta from the LLM stream.

    type = user:
        content = string (user message text) or list (multimodal content blocks)
        media   = list of local filesystem paths to media files attached by the user

    type = thinking / content:
        content = string

    type = tool_call:
        content = ToolCallRequest (complete, non-partial tool call)

    type = tool_call_results:
        content = list[ToolCallResult]
    """
    type: Literal["tool_call", "thinking", "content", "user", "tool_call_results", "system", "subagent_ref"]
    content: str | ToolCallRequest | list
    # Local filesystem paths saved alongside user messages for UI replay.
    # Only populated for type=="user"; empty for all other delta types.
    media: list[str] = field(default_factory=list)
    # UTC ISO 8601 timestamp recorded when the delta is persisted to the session.
    # Only set on final "content" deltas (assistant responses), never on streaming chunks.
    time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for session storage."""
        if self.type == "tool_call" and isinstance(self.content, ToolCallRequest):
            tc = self.content
            return {
                "type": self.type,
                "content": {
                    "index": tc.index, "id": tc.id,
                    "name": tc.name, "arguments": tc.arguments,
                },
            }
        if self.type == "tool_call_results" and isinstance(self.content, list):
            return {
                "type": self.type,
                "content": [
                    {"tool_call_id": r.tool_call_id, "name": r.name, "content": r.content}
                    for r in self.content if isinstance(r, ToolCallResult)
                ],
            }
        d: dict[str, Any] = {"type": self.type, "content": self.content}
        if self.media:
            d["media"] = self.media
        if self.time:
            d["time"] = self.time
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StreamDelta":
        """Deserialize from a dict (as stored in session JSONL)."""
        dtype = data.get("type", "content")
        raw = data.get("content", "")
        if dtype == "tool_call" and isinstance(raw, dict):
            raw = ToolCallRequest(
                index=raw.get("index", 0), id=raw.get("id", ""),
                name=raw.get("name", ""), arguments=raw.get("arguments", {}),
            )
        elif dtype == "tool_call_results" and isinstance(raw, list):
            raw = [
                ToolCallResult(
                    tool_call_id=r.get("tool_call_id", ""),
                    name=r.get("name", ""),
                    content=r.get("content", ""),
                )
                for r in raw if isinstance(r, dict)
            ]
        media = data.get("media") or []
        return cls(type=dtype, content=raw, media=media, time=data.get("time"))


def delta_to_openai(deltas: list[StreamDelta]) -> list[dict[str, Any]]:
    """Convert merged StreamDeltas into OpenAI chat-completion messages.

    Thinking deltas are excluded — they are for UI display only and must
    not be sent back to the provider.  Consecutive tool-call deltas within
    a single assistant turn are grouped into one ``assistant`` message.
    """
    messages: list[dict[str, Any]] = []
    content_buf: str | None = None
    tool_calls_buf: list[ToolCallRequest] = []

    def flush_assistant() -> None:
        nonlocal content_buf, tool_calls_buf
        if content_buf is None and not tool_calls_buf:
            return
        msg: dict[str, Any] = {"role": "assistant", "content": content_buf}
        if tool_calls_buf:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in tool_calls_buf
            ]
        messages.append(msg)
        content_buf = None
        tool_calls_buf = []

    for delta in deltas:
        if delta.type == "system":
            flush_assistant()
            messages.append({"role": "system", "content": delta.content})
        elif delta.type == "user":
            flush_assistant()
            messages.append({"role": "user", "content": delta.content})
        elif delta.type == "thinking":
            pass
        elif delta.type == "content":
            content_buf = delta.content
        elif delta.type == "tool_call" and isinstance(delta.content, ToolCallRequest):
            tool_calls_buf.append(delta.content)
        elif delta.type == "tool_call_results" and isinstance(delta.content, list):
            flush_assistant()
            for r in delta.content:
                if isinstance(r, ToolCallResult):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": r.tool_call_id,
                        "name": r.name,
                        "content": r.content,
                    })
        elif delta.type == "subagent_ref" and isinstance(delta.content, dict):
            flush_assistant()
            ref = delta.content
            announce = ref.get("announce")
            if announce and isinstance(announce, str):
                messages.append({"role": "user", "content": announce})
            else:
                status = "completed successfully" if ref.get("status") == "ok" else "failed"
                text = f"[Subagent '{ref.get('label', 'task')}' {status}]\nTask: {ref.get('task', '')}"
                messages.append({"role": "user", "content": text})

    flush_assistant()
    return messages


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
        deltas.append(StreamDelta(type="tool_call", content=tc))

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
        if delta.type == "tool_call":
            tc = delta.content
            if isinstance(tc, ToolCallRequest) and not tc.partial:
                tool_calls.append(tc)

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
