"""Integration test for OpenAIProvider streaming with config file credentials."""

import asyncio

from nekoclaw.config.manager import get_global_config
from nekoclaw.providers.openai_provider import OpenAIProvider


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "The city name, e.g. Tokyo",
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "Temperature unit",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text content to a file on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path of the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write into the file.",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "If true, append to the file instead of overwriting it.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]

MESSAGES = [
    {
        "role": "user",
        "content": "What's the weather like in Tokyo right now? Also, tell me where to travel in Tokyo. Also, write a very long novel of meeting a Japanese girl for me, save in `plan.md`",
    }
]


async def test_openai_streaming() -> None:
    config = get_global_config()
    provider_cfg = config.providers.openai

    print(f"api_key : {provider_cfg.api_key[:8]}..." if provider_cfg.api_key else "api_key : (not set)")
    print(f"api_base: {provider_cfg.api_base or '(default OpenAI)'}")
    print(f"model   : {config.agents.defaults.model}")
    print()

    provider = OpenAIProvider(
        default_model=config.agents.defaults.model,
        api_key=provider_cfg.api_key or None,
        api_base=provider_cfg.api_base or None,
        extra_headers=provider_cfg.extra_headers,
    )

    print("=== Streaming deltas ===")
    idx = 0
    async for delta in provider.chat_stream(
        messages=MESSAGES,
        tools=TOOLS,
        max_tokens=512,
        temperature=0.0,
    ):
        print(f"[{idx:03d}] type={delta.type!r:12s}  content={delta.content!r}")
        idx += 1

    print(f"\nTotal deltas received: {idx}")


if __name__ == "__main__":
    asyncio.run(test_openai_streaming())
