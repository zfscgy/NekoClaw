"""Nanochat web UI channel — serves a Vue.js chat frontend over HTTP + WebSocket."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import NanochatConfig

try:
    from aiohttp import web as aiohttp_web
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp_web = None  # type: ignore
    aiohttp = None  # type: ignore

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "nanochat" / "nanochat_frontend"
_FRONTEND_DIST = _FRONTEND_DIR / "dist"

# Token → cached path registry for serving files safely.
# Files are copied into a persistent cache dir at registration time so they
# remain downloadable even if the agent deletes or overwrites the originals.
_file_registry: dict[str, Path] = {}


def _get_file_cache_dir() -> Path:
    """Return (and create) the nanochat file-download cache directory."""
    from nanobot.config.paths import get_media_dir
    return get_media_dir("nanochat")


def _register_file(path: str) -> str:
    """Copy a file into the nanochat cache and return a stable URL token.

    The token is derived from the file content hash so that identical files
    share a cache entry, while re-generated files with new content get a
    fresh token and cache entry.
    """
    import hashlib
    src = Path(path).resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"Cannot register non-existent file: {src}")

    # Token = first 16 hex chars of the SHA-1 of the *file content*
    # so regenerated files (different content) get a new token/cache entry.
    file_hash = hashlib.sha1(src.read_bytes()).hexdigest()[:16]
    token = file_hash

    cache_dir = _get_file_cache_dir()
    # Preserve original filename in the cache so Content-Disposition is accurate
    cached = cache_dir / f"{token}_{src.name}"
    if not cached.exists():
        shutil.copy2(src, cached)
        logger.debug("Cached file token={} src={} -> {}", token, src, cached)
    else:
        logger.debug("File cache hit token={} path={}", token, cached)

    _file_registry[token] = cached
    return token


class NanochatChannel(BaseChannel):
    """Chat web UI channel serving Vue.js frontend + REST + WebSocket API."""

    name = "nanochat"

    def __init__(self, config: NanochatConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: NanochatConfig = config
        # conversation_id -> set of WebSocket connections
        self._ws_connections: dict[str, set] = {}
        # conversation_id -> list of message dicts (for history replay on reconnect)
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._app: Any = None
        self._runner: Any = None
        self._site: Any = None

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not AIOHTTP_AVAILABLE:
            logger.error("aiohttp not installed. Run: pip install aiohttp")
            return

        self._running = True
        self._app = aiohttp_web.Application()
        self._setup_routes()

        self._runner = aiohttp_web.AppRunner(self._app)
        await self._runner.setup()
        self._site = aiohttp_web.TCPSite(
            self._runner,
            host=self.config.host,
            port=self.config.port,
        )
        await self._site.start()
        logger.info(
            "Nanochat web UI started at http://{}:{}", self.config.host, self.config.port
        )

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._runner:
            await self._runner.cleanup()
        logger.info("Nanochat web UI stopped")

    @staticmethod
    def _media_to_urls(paths: list[str]) -> list[str]:
        """Convert filesystem paths to /file/{token} URLs served by this channel.

        External URLs (http/https/data:) are passed through unchanged.
        Local paths — regardless of where on the filesystem they live — are
        registered in the token registry and returned as /file/{token}?name=…
        so the frontend can download them via the same origin.
        """
        result = []
        for p in paths:
            if p.startswith("http://") or p.startswith("https://") or p.startswith("data:"):
                result.append(p)
                continue
            try:
                token = _register_file(p)
                name = Path(p).name
                result.append(f"/file/{token}?name={name}")
            except Exception:
                result.append(p)
        return result

    async def send(self, msg: OutboundMessage) -> None:
        """Push an outbound message to all WebSocket subscribers of the conversation."""
        conversation_id = msg.chat_id
        is_progress = bool(msg.metadata.get("_progress"))
        is_tool_hint = bool(msg.metadata.get("_tool_hint"))
        is_stream_token = bool(msg.metadata.get("_stream_token"))
        is_stream_think = bool(msg.metadata.get("_stream_think"))
        is_raw_response = bool(msg.metadata.get("_raw_response"))

        is_stream_tool_delta = bool(msg.metadata.get("_stream_tool_delta"))

        if is_stream_tool_delta:
            # Incremental tool-call argument delta — send as stream_tool_call_delta; never persist.
            await self._broadcast(conversation_id, {
                "type": "stream_tool_call_delta",
                "content": msg.content,
                "conversation_id": conversation_id,
            })
            return

        if is_stream_think:
            # A single streamed thinking chunk — send as stream_think_delta; never persist.
            await self._broadcast(conversation_id, {
                "type": "stream_think_delta",
                "content": msg.content,
                "conversation_id": conversation_id,
            })
            return

        if is_stream_token:
            # A single streamed chunk — send as stream_delta; never persist to history.
            await self._broadcast(conversation_id, {
                "type": "stream_delta",
                "role": "assistant",
                "content": msg.content,
                "conversation_id": conversation_id,
            })
            return

        if is_tool_hint:
            msg_type = "tool_call"
        elif is_progress:
            msg_type = "progress"
        elif is_raw_response:
            # Plain LLM text response (not via the message tool) — the frontend
            # routes this into the actions panel when reasoning was present.
            msg_type = "raw_response"
        else:
            msg_type = "message"

        payload: dict[str, Any] = {
            "type": msg_type,
            "role": "assistant",
            "content": msg.content,
            "media": self._media_to_urls(msg.media or []),
            "conversation_id": conversation_id,
        }

        if not is_progress:
            # Persist final messages to history
            self._history.setdefault(conversation_id, []).append(payload)
            # Clear the streaming bubble before delivering the canonical message.
            await self._broadcast(conversation_id, {
                "type": "stream_end",
                "conversation_id": conversation_id,
            })

        await self._broadcast(conversation_id, payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, conversation_id: str, payload: dict) -> None:
        """Send a JSON payload to all WebSocket clients of a conversation."""
        sockets = self._ws_connections.get(conversation_id, set())
        if not sockets:
            return
        data = json.dumps(payload, ensure_ascii=False)
        dead: set = set()
        for ws in list(sockets):
            try:
                await ws.send_str(data)
            except Exception:
                dead.add(ws)
        sockets -= dead

    def _append_user_message(self, conversation_id: str, content: str, media: list[str]) -> None:
        entry: dict[str, Any] = {
            "type": "message",
            "role": "user",
            "content": content,
            "media": media,
            "conversation_id": conversation_id,
        }
        self._history.setdefault(conversation_id, []).append(entry)

    # ------------------------------------------------------------------
    # Route setup
    # ------------------------------------------------------------------

    def _setup_routes(self) -> None:
        app = self._app
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws/{conversation_id}", self._handle_ws)
        app.router.add_get("/api/conversations", self._handle_list_conversations)
        app.router.add_post("/api/conversations", self._handle_new_conversation)
        app.router.add_get("/api/conversations/{conversation_id}/history", self._handle_history)
        app.router.add_post("/api/conversations/{conversation_id}/message", self._handle_message)
        app.router.add_post("/api/conversations/{conversation_id}/command", self._handle_command)
        # Serve registered files by token (works for any absolute path on the filesystem)
        app.router.add_get("/file/{token}", self._handle_file)
        # Serve frontend static assets
        app.router.add_get("/assets/{path:.*}", self._handle_assets)

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _handle_index(self, request: Any) -> Any:
        # Prefer built Vite output; fall back to legacy index.html
        html_path = _FRONTEND_DIST / "index.html" if _FRONTEND_DIST.exists() else _FRONTEND_DIR / "index.html"
        if html_path.exists():
            return aiohttp_web.FileResponse(html_path)
        return aiohttp_web.Response(text="Nanochat frontend not found.", status=404)

    async def _handle_assets(self, request: Any) -> Any:
        rel = request.match_info["path"]
        if _FRONTEND_DIST.exists():
            path = _FRONTEND_DIST / "assets" / rel
        else:
            path = _FRONTEND_DIR / rel
        if path.exists() and path.is_file():
            mime, _ = mimetypes.guess_type(str(path))
            return aiohttp_web.FileResponse(path, headers={"Content-Type": mime or "application/octet-stream"})
        return aiohttp_web.Response(text="Not found", status=404)

    async def _handle_file(self, request: Any) -> Any:
        """Serve a cached file by its content-hash token.

        Files are copied into a persistent cache at send-time so they remain
        downloadable even after the agent deletes or regenerates the originals.
        On a server restart the in-memory registry is empty; we recover by
        scanning the cache directory for a matching token prefix.
        """
        token = request.match_info["token"]
        file_path = _file_registry.get(token)

        # Registry miss (e.g. after server restart) — scan the cache dir
        if file_path is None:
            try:
                cache_dir = _get_file_cache_dir()
                matches = list(cache_dir.glob(f"{token}_*"))
                if matches:
                    file_path = matches[0]
                    _file_registry[token] = file_path
                    logger.debug("Recovered file from cache: token={} path={}", token, file_path)
            except Exception:
                pass

        if file_path is None or not file_path.exists() or not file_path.is_file():
            logger.warning("File not found for token={}", token)
            return aiohttp_web.Response(text="Not found", status=404)

        mime, _ = mimetypes.guess_type(str(file_path))
        # Prefer the ?name= query param; fall back to stripping the token prefix from the cache filename
        download_name = request.rel_url.query.get("name") or "_".join(file_path.name.split("_")[1:]) or file_path.name
        headers = {
            "Content-Type": mime or "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{download_name}"',
        }
        return aiohttp_web.FileResponse(file_path, headers=headers)

    async def _handle_list_conversations(self, request: Any) -> Any:
        conversations = []
        for cid, history in self._history.items():
            last = next(
                (m for m in reversed(history) if m.get("type") == "message"),
                None,
            )
            conversations.append({
                "id": cid,
                "last_message": last.get("content", "")[:80] if last else "",
                "message_count": sum(1 for m in history if m.get("type") == "message"),
            })
        return aiohttp_web.json_response({"conversations": conversations})

    async def _handle_new_conversation(self, request: Any) -> Any:
        data = await request.json() if request.body_exists else {}
        cid = data.get("id") or str(uuid.uuid4())[:8]
        self._history.setdefault(cid, [])
        return aiohttp_web.json_response({"conversation_id": cid})

    async def _handle_history(self, request: Any) -> Any:
        cid = request.match_info["conversation_id"]
        history = self._history.get(cid, [])
        return aiohttp_web.json_response({"history": history})

    async def _handle_message(self, request: Any) -> Any:
        cid = request.match_info["conversation_id"]
        try:
            data = await request.json()
        except Exception:
            return aiohttp_web.json_response({"error": "invalid JSON"}, status=400)

        content = (data.get("content") or "").strip()
        media: list[str] = data.get("media") or []
        if not content and not media:
            return aiohttp_web.json_response({"error": "empty message"}, status=400)

        self._append_user_message(cid, content, media)
        # Echo back to all WS clients (so other tabs see user message)
        await self._broadcast(cid, {
            "type": "message",
            "role": "user",
            "content": content,
            "media": media,
            "conversation_id": cid,
        })

        await self._handle_message_internal(cid, content, media)
        return aiohttp_web.json_response({"ok": True})

    async def _handle_command(self, request: Any) -> Any:
        cid = request.match_info["conversation_id"]
        try:
            data = await request.json()
        except Exception:
            return aiohttp_web.json_response({"error": "invalid JSON"}, status=400)

        cmd = (data.get("command") or "").strip()
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        await self._handle_message_internal(cid, cmd, [])
        return aiohttp_web.json_response({"ok": True})

    async def _handle_message_internal(
        self, conversation_id: str, content: str, media: list[str]
    ) -> None:
        await self._broadcast(conversation_id, {
            "type": "stream_start",
            "conversation_id": conversation_id,
        })
        await self._handle_message(
            sender_id="user",
            chat_id=conversation_id,
            content=content,
            media=media,
            metadata={"conversation_id": conversation_id, "_streaming": True},
        )

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def _handle_ws(self, request: Any) -> Any:
        cid = request.match_info["conversation_id"]
        ws = aiohttp_web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        self._ws_connections.setdefault(cid, set()).add(ws)
        logger.debug("WebSocket connected for conversation {}", cid)

        # Replay history on connect
        for entry in self._history.get(cid, []):
            try:
                await ws.send_str(json.dumps(entry, ensure_ascii=False))
            except Exception:
                break

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue

                    msg_type = data.get("type")
                    if msg_type == "message":
                        content = (data.get("content") or "").strip()
                        media = data.get("media") or []
                        if content or media:
                            self._append_user_message(cid, content, media)
                            # Broadcast user message to other subscribers
                            await self._broadcast(cid, {
                                "type": "message",
                                "role": "user",
                                "content": content,
                                "media": media,
                                "conversation_id": cid,
                            })
                            await self._handle_message_internal(cid, content, media)
                    elif msg_type == "command":
                        cmd = (data.get("command") or "").strip()
                        if cmd:
                            if not cmd.startswith("/"):
                                cmd = "/" + cmd
                            await self._handle_message_internal(cid, cmd, [])
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        finally:
            self._ws_connections.get(cid, set()).discard(ws)
            logger.debug("WebSocket disconnected for conversation {}", cid)

        return ws

    # Override BaseChannel._handle_message to skip allow_from check for "user"
    # since all access is local — defer to config.allow_from only if it has entries.
    async def _handle_message(  # type: ignore[override]
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        from nanobot.bus.events import InboundMessage

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
            session_key_override=session_key,
        )
        await self.bus.publish_inbound(msg)
