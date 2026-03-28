"""LiteLLM provider implementation for multi-provider support."""

import hashlib
import json
import os
import secrets
import string
from typing import Any, AsyncIterator

import json_repair
import litellm
from litellm import acompletion
from loguru import logger

from nanobot.providers.base import LLMProvider, StreamDelta, ToolCallRequest, build_stream_deltas
from nanobot.providers.registry import find_by_model, find_gateway

# Kept for import compatibility; no longer raised by chat_stream.
class _ToolCallsDetected(Exception):
    """Deprecated sentinel — no longer raised; kept for backwards compatibility."""


# Standard chat-completion message keys.
_ALLOWED_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"})
_ANTHROPIC_EXTRA_KEYS = frozenset({"thinking_blocks"})
_ALNUM = string.ascii_letters + string.digits

def _short_tool_id() -> str:
    """Generate a 9-char alphanumeric ID compatible with all providers (incl. Mistral)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}

        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)

        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)

        if api_base:
            litellm.api_base = api_base

        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True

    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return
        if not spec.env_key:
            # OAuth/provider-only specs (for example: openai_codex)
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        # Standard mode: auto-prefix for known providers
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            model = self._canonicalize_explicit_prefix(model, spec.name, spec.litellm_prefix)
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"

        return model

    @staticmethod
    def _canonicalize_explicit_prefix(model: str, spec_name: str, canonical_prefix: str) -> str:
        """Normalize explicit provider prefixes like `github-copilot/...`."""
        if "/" not in model:
            return model
        prefix, remainder = model.split("/", 1)
        if prefix.lower().replace("-", "_") != spec_name:
            return model
        return f"{canonical_prefix}/{remainder}"

    def _supports_cache_control(self, model: str) -> bool:
        """Return True when the provider supports cache_control on content blocks."""
        if self._gateway is not None:
            return self._gateway.supports_prompt_caching
        spec = find_by_model(model)
        return spec is not None and spec.supports_prompt_caching

    def _apply_cache_control(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Return copies of messages and tools with cache_control injected."""
        new_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg["content"]
                if isinstance(content, str):
                    new_content = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
                else:
                    new_content = list(content)
                    new_content[-1] = {**new_content[-1], "cache_control": {"type": "ephemeral"}}
                new_messages.append({**msg, "content": new_content})
            else:
                new_messages.append(msg)

        new_tools = tools
        if tools:
            new_tools = list(tools)
            new_tools[-1] = {**new_tools[-1], "cache_control": {"type": "ephemeral"}}

        return new_messages, new_tools

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

    @staticmethod
    def _extra_msg_keys(original_model: str, resolved_model: str) -> frozenset[str]:
        """Return provider-specific extra keys to preserve in request messages."""
        spec = find_by_model(original_model) or find_by_model(resolved_model)
        if (spec and spec.name == "anthropic") or "claude" in original_model.lower() or resolved_model.startswith("anthropic/"):
            return _ANTHROPIC_EXTRA_KEYS
        return frozenset()

    @staticmethod
    def _normalize_tool_call_id(tool_call_id: Any) -> Any:
        """Normalize tool_call_id to a provider-safe 9-char alphanumeric form."""
        if not isinstance(tool_call_id, str):
            return tool_call_id
        if len(tool_call_id) == 9 and tool_call_id.isalnum():
            return tool_call_id
        return hashlib.sha1(tool_call_id.encode()).hexdigest()[:9]

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]], extra_keys: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        """Strip non-standard keys and ensure assistant messages have a content key."""
        allowed = _ALLOWED_MSG_KEYS | extra_keys
        sanitized = LLMProvider._sanitize_request_messages(messages, allowed)
        id_map: dict[str, str] = {}

        def map_id(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            return id_map.setdefault(value, LiteLLMProvider._normalize_tool_call_id(value))

        for clean in sanitized:
            # Keep assistant tool_calls[].id and tool tool_call_id in sync after
            # shortening, otherwise strict providers reject the broken linkage.
            if isinstance(clean.get("tool_calls"), list):
                normalized_tool_calls = []
                for tc in clean["tool_calls"]:
                    if not isinstance(tc, dict):
                        normalized_tool_calls.append(tc)
                        continue
                    tc_clean = dict(tc)
                    tc_clean["id"] = map_id(tc_clean.get("id"))
                    normalized_tool_calls.append(tc_clean)
                clean["tool_calls"] = normalized_tool_calls

            if "tool_call_id" in clean and clean["tool_call_id"]:
                clean["tool_call_id"] = map_id(clean["tool_call_id"])
        return sanitized

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> list[StreamDelta]:
        """
        Send a chat completion request via LiteLLM.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            Complete response as a list of ``StreamDelta`` objects.
        """
        original_model = model or self.default_model
        model = self._resolve_model(original_model)
        extra_msg_keys = self._extra_msg_keys(original_model, model)

        if self._supports_cache_control(original_model):
            messages, tools = self._apply_cache_control(messages, tools)

        # Clamp max_tokens to at least 1 — negative or zero values cause
        # LiteLLM to reject the request with "max_tokens must be at least 1".
        max_tokens = max(1, max_tokens)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages), extra_keys=extra_msg_keys),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
        self._apply_model_overrides(model, kwargs)

        # Pass api_key directly — more reliable than env vars alone
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Pass api_base for custom endpoints
        if self.api_base:
            kwargs["api_base"] = self.api_base

        # Pass extra headers (e.g. APP-Code for AiHubMix)
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
            kwargs["drop_params"] = True
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            return [StreamDelta(type="content", content=f"Error calling LLM: {str(e)}")]

    def _parse_response(self, response: Any) -> list[StreamDelta]:
        """Parse LiteLLM response into complete stream-style deltas."""
        choice = response.choices[0]
        message = choice.message
        content = message.content

        # Some providers (e.g. GitHub Copilot) split content and tool_calls
        # across multiple choices. Merge them so tool_calls are not lost.
        raw_tool_calls = []
        for ch in response.choices:
            msg = ch.message
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                raw_tool_calls.extend(msg.tool_calls)
            if not content and msg.content:
                content = msg.content

        if len(response.choices) > 1:
            logger.debug("LiteLLM response has {} choices, merged {} tool_calls",
                         len(response.choices), len(raw_tool_calls))

        tool_calls = []
        for tc in raw_tool_calls:
            # Parse arguments from JSON string if needed
            args = tc.function.arguments
            if isinstance(args, str):
                args = json_repair.loads(args)

            tool_calls.append(ToolCallRequest(
                id=_short_tool_id(),
                name=tc.function.name,
                arguments=args,
            ))

        reasoning_content = getattr(message, "reasoning_content", None) or None
        thinking_blocks = getattr(message, "thinking_blocks", None) or None

        return build_stream_deltas(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
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
        """Stream structured response deltas via LiteLLM streaming.

        Only streams the final (non-tool-call) assistant turn.  If the first
        response contains tool calls we fall back to the regular non-streaming
        ``chat()`` path so the tool loop still works correctly. Yields
        ``StreamDelta`` objects as they arrive from the provider.
        """
        original_model = model or self.default_model
        resolved_model = self._resolve_model(original_model)
        extra_msg_keys = self._extra_msg_keys(original_model, resolved_model)

        if self._supports_cache_control(original_model):
            messages, tools = self._apply_cache_control(messages, tools)

        max_tokens = max(1, max_tokens)

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages), extra_keys=extra_msg_keys),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        self._apply_model_overrides(resolved_model, kwargs)

        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
            kwargs["drop_params"] = True
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # index → {"id": str, "name": str, "arguments": str}
        accumulated_tool_calls: dict[int, dict[str, str]] = {}
        # Indices whose complete tool-call chunk has already been yielded.
        finalized_indices: set[int] = set()

        try:
            # logger.info(f"LiteLLM messages: \n{json.dumps(kwargs['messages'], indent=2, ensure_ascii=False)}")
            # logger.info(f"LiteLLLM tools: \n{json.dumps(kwargs['tools'], indent=2, ensure_ascii=False)}")
            stream = await acompletion(**kwargs)
            async for chunk in stream:
                # logger.info(f"LiteLLM chunk: {chunk}")
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                if getattr(delta, "tool_calls", None):
                    for tc_delta in delta.tool_calls:
                        idx = getattr(tc_delta, "index", 0) or 0

                        # When a higher index appears all lower indices are complete.
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

                        # Stream a delta so callers can show live argument build-up.
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

                # Anthropic extended thinking / DeepSeek reasoning deltas
                thinking_text = (
                    getattr(delta, "reasoning_content", None)
                    or getattr(delta, "thinking", None)
                )
                if thinking_text:
                    yield StreamDelta(type="thinking", content=thinking_text)

                text = getattr(delta, "content", None)
                if text:
                    yield StreamDelta(type="content", content=text)

            # Finalize any tool calls not yet yielded (the last index or sole call).
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
        """Get the default model."""
        return self.default_model
