"""Integration test for streaming output (think + response tokens) using the real model."""

from __future__ import annotations

import asyncio
import pytest


def _load_real_provider():
    """Load OpenAIProvider from the user's nekoclaw config (~/.nekoclaw/config.json)."""
    from nekoclaw.config.loader import load_config
    from nekoclaw.providers.openai_provider import OpenAIProvider

    cfg = load_config()
    model = cfg.agents.defaults.model
    provider_cfg = cfg.get_provider(model)

    if provider_cfg is None or not provider_cfg.api_key:
        pytest.skip("No provider API key found in ~/.nekoclaw/config.json — skipping real-model test.")

    return OpenAIProvider(
        api_key=provider_cfg.api_key,
        api_base=provider_cfg.api_base or None,
        default_model=model,
        extra_headers=provider_cfg.extra_headers,
    ), model, cfg.agents.defaults.reasoning_effort


@pytest.mark.asyncio
async def test_streaming_tokens_printed():
    """chat_stream() yields content deltas and prints each one."""
    provider, model, _ = _load_real_provider()

    messages = [{"role": "user", "content": "Say hello in exactly five words."}]

    chunks: list[str] = []
    print(f"\n[streaming] model={model}")
    print("[streaming] --- response tokens ---")

    async for chunk in provider.chat_stream(messages=messages, model=model):
        print(repr(chunk), flush=True)
        if chunk.type == "content":
            chunks.append(chunk.content)

    full_text = "".join(chunks)
    print(f"[streaming] --- full response ---\n{full_text}")

    assert chunks, "Expected at least one streamed chunk but got none."
    assert full_text.strip(), "Expected non-empty response text."


@pytest.mark.asyncio
async def test_raw_delta_fields():
    """Bypass chat_stream and print every field on every raw OpenAI delta.

    Run this to discover which attribute carries the reasoning tokens for
    your specific model so we can fix chat_stream.
    """
    from openai import AsyncOpenAI

    provider, model, reasoning_effort = _load_real_provider()

    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "What is 17 * 23? Think step by step."}],
        "max_tokens": 1024,
        "temperature": 0.1,
        "stream": True,
    }
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort

    client = AsyncOpenAI(
        api_key=provider.api_key or "no-key",
        base_url=provider.api_base or None,
        default_headers=provider.extra_headers or {},
    )

    print(f"\n[raw_delta] model={model}  reasoning_effort={reasoning_effort}")
    print("[raw_delta] --- raw delta attributes per chunk ---")

    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        print("--- new chunk ---")
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue
        attrs = {k: v for k, v in delta.to_dict().items() if v is not None}
        if attrs:
            print(attrs, flush=True)

    print("[raw_delta] --- done ---")


@pytest.mark.asyncio
async def test_streaming_think_and_response_printed():
    """chat_stream() emits thinking and content deltas for reasoning models.

    The test prints every segment so you can observe the live token-by-token output.
    Non-reasoning models will produce zero think chunks (which is fine).
    """
    provider, model, reasoning_effort = _load_real_provider()

    messages = [
        {
            "role": "user",
            "content": (
                "Think step-by-step: what is 17 * 23? "
                "Show your reasoning then give the final numeric answer."
            ),
        }
    ]

    think_chunks: list[str] = []
    response_chunks: list[str] = []

    print(f"\n[streaming] model={model}  reasoning_effort={reasoning_effort}")
    print("[streaming] --- segments (raw) ---")

    async for chunk in provider.chat_stream(
        messages=messages,
        model=model,
        reasoning_effort=reasoning_effort,
    ):
        print(repr(chunk), flush=True)
        if chunk.type == "thinking":
            think_chunks.append(chunk.content)
        elif chunk.type == "content":
            response_chunks.append(chunk.content)

    full_think = "".join(think_chunks)
    full_response = "".join(response_chunks)

    print("\n[streaming] --- think ---")
    print(full_think or "(none — model does not support visible reasoning)")
    print("\n[streaming] --- response ---")
    print(full_response)

    assert response_chunks, "Expected at least one response chunk but got none."
    assert full_response.strip(), "Expected non-empty response text."
    # If the model emitted think tokens, they must be non-empty strings.
    for t in think_chunks:
        assert isinstance(t, str) and len(t) > 0


@pytest.mark.asyncio
async def test_streaming_via_agent_loop():
    """_run_agent_loop() calls on_delta for each streamed chunk.

    This exercises the full pipeline: AgentLoop → provider.chat_stream()
    → on_delta callback, printing every segment.
    """
    from pathlib import Path
    from unittest.mock import patch

    from nekoclaw.agent.loop import AgentLoop
    from nekoclaw.bus.queue import MessageBus

    provider, model, reasoning_effort = _load_real_provider()

    bus = MessageBus()

    with patch("nekoclaw.agent.loop.ContextBuilder"), \
         patch("nekoclaw.agent.loop.SessionManager"), \
         patch("nekoclaw.agent.loop.SubagentManager") as MockSubMgr:
        MockSubMgr.return_value.cancel_by_session.return_value = 0
        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=Path("/tmp"),
            model=model,
            reasoning_effort=reasoning_effort,
        )

    token_chunks: list[str] = []
    think_chunks: list[str] = []

    async def on_delta(delta) -> None:
        print(f"[delta] {repr(delta)}", flush=True)
        if delta.type == "content":
            token_chunks.append(delta.content)
        elif delta.type == "thinking":
            think_chunks.append(delta.content)

    from nekoclaw.providers.base import StreamDelta

    messages = [StreamDelta(type="user", content="Count from 1 to 5, one number per line.")]

    print(f"\n[agent_loop] model={model}  reasoning_effort={reasoning_effort}")
    final_content, tools_used, _ = await loop._run_agent_loop(
        initial_messages=messages,
        on_delta=on_delta,
    )

    full_tokens = "".join(token_chunks)
    full_think = "".join(think_chunks)

    print(f"\n[agent_loop] --- think ---\n{full_think or '(none)'}")
    print(f"[agent_loop] --- response (streamed) ---\n{full_tokens}")
    print(f"[agent_loop] --- final_content ---\n{final_content}")

    assert token_chunks or final_content, "Expected streamed tokens or a final content string."
    if token_chunks:
        assert full_tokens.strip(), "Streamed tokens should contain non-empty text."
    if final_content:
        assert final_content.strip(), "final_content should be non-empty."
    for t in think_chunks:
        assert isinstance(t, str) and len(t) > 0


if __name__ == "__main__":
    asyncio.run(test_streaming_think_and_response_printed())
