"""Web tools: web_search and web_fetch."""

import asyncio
import html
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.tools.web import lightsear_search, lightsear_fetch


class WebSearchTool(Tool):
    """Search the web using DuckDuckGo (no API key required)."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Results (1-50)", "minimum": 1, "maximum": 50}
        },
        "required": ["query"]
    }

    def __init__(self, max_results: int = 10):
        self.max_results = max_results

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        n = min(max(count or self.max_results, 1), 50)
        try:
            results = await asyncio.to_thread(lightsear_search, query)
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results, 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')} ({item.get('source', '')})")
                if body := item.get("body"):
                    lines.append(f"   {body}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("WebSearch error: {}", e)
            return f"Error: {e}"


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""

    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML → markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100}
        },
        "required": ["url"]
    }

    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars

    async def execute(self, url: str, extractMode: str = "markdown", maxChars: int | None = None, **kwargs: Any) -> str:
        try:
            content = await asyncio.to_thread(lightsear_fetch, url, extractMode)
            if not content:
                return f"No content for: {url}"
            return content
        except Exception as e:
            logger.error("WebFetch error: {}", e); return f"Error: {e}"
