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
from urllib.parse import quote

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
    """Chat web UI channel serving Vue.js frontend + REST + WebSocket API.

    Conversation history is stored in the standard agent session JSONL files
    (``~/.nanobot/sessions/nanochat_<cid>.jsonl``), which are the single source
    of truth for both the LLM context and the UI display.  Thinking/reasoning
    blocks are stored as ``_ui_only: true`` entries inside those same files so
    they survive restarts without ever being fed back to the model.
    """

    name = "nanochat"

    def __init__(self, config: NanochatConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: NanochatConfig = config
        # conversation_id -> set of active WebSocket connections
        self._ws_connections: dict[str, set] = {}
        # conversation_ids with an LLM turn currently in-flight
        self._active_streams: set[str] = set()
        # conversation_id -> ordered list of WS payload dicts representing the
        # in-flight turn's committed rounds (stream_think_delta + stream_end +
        # tool_call per round).  Used to replay mid-turn history when a new
        # WebSocket client reconnects while generation is still running.
        self._stream_segments: dict[str, list[dict]] = {}
        # Per-conversation accumulation of the *current* in-progress LLM round
        # (reset each time a round commits via stream_end + tool_call).
        # Shape:
        # {
        #   "pending_user": {"content": str, "media": list[str]},
        #   "items": [
        #       {"kind": "thinking", "content": str},
        #       {"kind": "content", "content": str},
        #       {"kind": "tool_call", "index": int, "name": str, "arguments": str},
        #   ],
        #   "tool_deltas": {index: {name, arguments}},
        # }
        self._cur_round: dict[str, dict] = {}
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

    # ------------------------------------------------------------------
    # Session helpers — read & translate session JSONL to UI format
    # ------------------------------------------------------------------

    @staticmethod
    def _session_path(cid: str) -> Path:
        from nanobot.config.paths import get_sessions_dir
        from nanobot.utils.helpers import safe_filename
        safe_key = safe_filename(f"nanochat_{cid}")
        return get_sessions_dir() / f"{safe_key}.jsonl"

    @staticmethod
    def _read_session_messages(cid: str) -> list[dict[str, Any]]:
        """Read raw messages from the session JSONL for this conversation."""
        path = NanochatChannel._session_path(cid)
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        continue
                    messages.append(data)
        except Exception as exc:
            logger.warning("Failed to read session for {}: {}", cid, exc)
        return messages

    @staticmethod
    def _session_to_ui(cid: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate raw session messages into nanochat UI-format entries.

        Mapping:
          _ui_only entries (streaming thinking)  → type: think
          role: user                              → type: content, role: user
          role: assistant + tool_calls            → type: tool_call  (one per call)
          role: assistant + content               → type: content, role: assistant
          role: assistant + reasoning_content     → type: think before the content
          role: tool / system                     → skipped
        """
        ui: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "")

            # ── Streaming-path thinking stored as _ui_only entries ──────────
            if m.get("_ui_only"):
                content = (m.get("content") or "").strip()
                if content:
                    ui.append({
                        "type": "think",
                        "role": "assistant",
                        "content": content,
                        "media": [],
                        "conversation_id": cid,
                    })
                continue

            # ── User messages ────────────────────────────────────────────────
            if role == "user":
                raw = m.get("content", "")
                if isinstance(raw, list):
                    # Multimodal: flatten text parts
                    raw = " ".join(
                        c.get("text", "") for c in raw
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                if raw:
                    ui.append({
                        "type": "content",
                        "role": "user",
                        "content": raw,
                        "media": [],
                        "conversation_id": cid,
                    })

            # ── Assistant messages ───────────────────────────────────────────
            elif role == "assistant":
                if m.get("_message_ack"):
                    continue

                # Blocking-path thinking: emit as progress before the message
                think = (m.get("reasoning_content") or "").strip()
                if not think:
                    blocks = m.get("thinking_blocks")
                    if isinstance(blocks, str):
                        think = blocks.strip()
                    elif isinstance(blocks, list):
                        parts = []
                        for b in blocks:
                            if isinstance(b, dict):
                                for k in ("thinking", "content", "text"):
                                    if b.get(k):
                                        parts.append(str(b[k]))
                                        break
                        think = "\n".join(parts).strip()
                if think:
                    ui.append({
                        "type": "think",
                        "role": "assistant",
                        "content": think,
                        "media": [],
                        "conversation_id": cid,
                    })

                def _append_tool_calls() -> None:
                    tool_calls = m.get("tool_calls") or []
                    if tool_calls:
                        for tc in tool_calls:
                            fn = tc.get("function", {})
                            name = fn.get("name", "tool")
                            args_str = fn.get("arguments", "")
                            if name == "message":
                                try:
                                    args = json.loads(args_str)
                                    if isinstance(args, dict) and isinstance(args.get("media"), list):
                                        args = dict(args)
                                        args["media"] = NanochatChannel._media_to_urls(args["media"])
                                        args_str = json.dumps(args, ensure_ascii=False)
                                except Exception:
                                    pass
                            ui.append({
                                "type": "tool_call",
                                "role": "assistant",
                                "content": f"{name}({args_str})",
                                "media": [],
                                "conversation_id": cid,
                            })

                def _append_content() -> None:
                    if m.get("content"):
                        ui.append({
                            "type": "content",
                            "role": "assistant",
                            "content": m.get("content"),
                            "media": [],
                            "conversation_id": cid,
                        })

                ui_segments = m.get("_ui_segments")
                if ui_segments == ["content", "tool_calls"]:
                    _append_content()
                    _append_tool_calls()
                    continue

                _append_tool_calls()
                _append_content()

            # role == "tool" or "system": skip — internal plumbing, not shown in UI

        return ui

    # ------------------------------------------------------------------
    # Media helpers
    # ------------------------------------------------------------------

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
                result.append(f"/file/{token}?name={quote(name)}")
            except Exception:
                result.append(p)
        return result


    # ------------------------------------------------------------------
    # Outbound message handler
    # ------------------------------------------------------------------

    async def send(self, msg: OutboundMessage) -> None:
        """Push an outbound message to all WebSocket subscribers of the conversation."""
        conversation_id = msg.chat_id
        is_progress = bool(msg.metadata.get("_progress"))
        is_tool_hint = bool(msg.metadata.get("_tool_hint"))
        is_stream_token = bool(msg.metadata.get("_stream_token"))
        is_stream_think = bool(msg.metadata.get("_stream_think"))
        is_raw_response = bool(msg.metadata.get("_raw_response"))
        is_stream_tool_delta = bool(msg.metadata.get("_stream_tool_delta"))
        is_message_tool_delivery = bool(msg.metadata.get("_sent_via_message_tool"))

        if is_message_tool_delivery:
            return

        is_stream_done = bool(msg.metadata.get("_stream_done"))
        if is_stream_done:
            # Session has been saved; discard all streaming replay state so that
            # reconnecting clients read from the session file instead of replaying
            # a stale in-flight buffer.
            self._active_streams.discard(conversation_id)
            self._stream_segments.pop(conversation_id, None)
            self._cur_round.pop(conversation_id, None)
            return

        if is_stream_tool_delta:
            # Accumulate latest full state per tool-call index for reconnect replay.
            try:
                delta = json.loads(msg.content)
                idx = delta.get("index", 0)
                cur = self._cur_round.setdefault(conversation_id, {})
                name = delta.get("name", "")
                arguments = delta.get("arguments", "")
                cur.setdefault("tool_deltas", {})[idx] = {
                    "name": name,
                    "arguments": arguments,
                }
                self._upsert_cur_round_tool_item(cur, idx, name, arguments)
            except Exception:
                pass
            await self._broadcast(conversation_id, {
                "type": "stream_tool_call_delta",
                "content": msg.content,
                "conversation_id": conversation_id,
            })
            return

        if is_stream_think:
            # Accumulate thinking text for reconnect replay.
            # Persistence is handled by AgentLoop._save_turn which writes a
            # _ui_only entry to the session JSONL after each round completes.
            cur = self._cur_round.setdefault(conversation_id, {})
            self._append_cur_round_text_item(cur, "thinking", msg.content)
            await self._broadcast(conversation_id, {
                "type": "stream_think_delta",
                "content": msg.content,
                "conversation_id": conversation_id,
            })
            return

        if is_stream_token:
            # Accumulate content text for reconnect replay.
            cur = self._cur_round.setdefault(conversation_id, {})
            self._append_cur_round_text_item(cur, "content", msg.content)
            await self._broadcast(conversation_id, {
                "type": "stream_content_delta",
                "role": "assistant",
                "content": msg.content,
                "conversation_id": conversation_id,
            })
            return

        if is_tool_hint:
            msg_type = "tool_call"
        elif is_progress:
            msg_type = "think"
        elif is_raw_response:
            msg_type = "raw_response"
        else:
            msg_type = "content"

        payload: dict[str, Any] = {
            "type": msg_type,
            "role": "assistant",
            "content": msg.content,
            "media": self._media_to_urls(msg.media or []),
            "conversation_id": conversation_id,
        }

        # tool_call and final messages (not plain progress) need a stream_end
        # signal so the frontend flushes its live streaming panel.
        should_signal = not is_progress or is_tool_hint
        if should_signal:
            if is_tool_hint:
                # Commit the current round into _stream_segments before clearing it.
                # Preserve the round's original live ordering before flushing it.
                cur = self._cur_round.pop(conversation_id, {})
                segs = self._stream_segments.setdefault(conversation_id, [])
                segs.extend(self._cur_round_to_replay_payloads(conversation_id, cur))
                segs.append({"type": "stream_end", "conversation_id": conversation_id})
            elif msg_type in ("content", "raw_response"):
                # Turn is done; discard all replay state.
                self._active_streams.discard(conversation_id)
                self._stream_segments.pop(conversation_id, None)
                self._cur_round.pop(conversation_id, None)

            await self._broadcast(conversation_id, {
                "type": "stream_end",
                "conversation_id": conversation_id,
            })

        # Append committed tool_call to segments so reconnecting clients see it
        # in the correct position (after the preceding stream_end).
        if is_tool_hint:
            self._stream_segments.setdefault(conversation_id, []).append(payload)

        await self._broadcast(conversation_id, payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _append_cur_round_text_item(cur: dict[str, Any], kind: str, content: str) -> None:
        """Append a streaming text item while preserving adjacency order."""
        if not content:
            return
        items = cur.setdefault("items", [])
        last = items[-1] if items else None
        if last and last.get("kind") == kind:
            last["content"] = (last.get("content") or "") + content
            return
        items.append({"kind": kind, "content": content})

    @staticmethod
    def _upsert_cur_round_tool_item(
        cur: dict[str, Any], index: int, name: str, arguments: str
    ) -> None:
        """Keep the first-seen tool-call position while updating its latest state."""
        items = cur.setdefault("items", [])
        for item in items:
            if item.get("kind") == "tool_call" and item.get("index") == index:
                item["name"] = name
                item["arguments"] = arguments
                return
        items.append({
            "kind": "tool_call",
            "index": index,
            "name": name,
            "arguments": arguments,
        })

    @staticmethod
    def _cur_round_to_replay_payloads(
        conversation_id: str, cur: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Serialize the current round into replayable WS payloads in arrival order."""
        payloads: list[dict[str, Any]] = []
        for item in cur.get("items", []):
            kind = item.get("kind")
            if kind == "thinking" and item.get("content"):
                payloads.append({
                    "type": "stream_think_delta",
                    "content": item["content"],
                    "conversation_id": conversation_id,
                })
            elif kind == "content" and item.get("content"):
                payloads.append({
                    "type": "stream_content_delta",
                    "role": "assistant",
                    "content": item["content"],
                    "conversation_id": conversation_id,
                })
            elif kind == "tool_call":
                payloads.append({
                    "type": "stream_tool_call_delta",
                    "content": json.dumps({
                        "index": item.get("index", 0),
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    }, ensure_ascii=False),
                    "conversation_id": conversation_id,
                })
        return payloads

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
        app.router.add_delete("/api/conversations/{conversation_id}", self._handle_delete_conversation)
        app.router.add_post("/api/conversations/{conversation_id}/message", self._handle_message)
        app.router.add_post("/api/conversations/{conversation_id}/command", self._handle_command)
        app.router.add_get("/file/{token}", self._handle_file)
        app.router.add_get("/assets/{path:.*}", self._handle_assets)

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _handle_index(self, request: Any) -> Any:
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
        """Serve a cached file by its content-hash token."""
        token = request.match_info["token"]
        file_path = _file_registry.get(token)

        if file_path is None:
            try:
                cache_dir = _get_file_cache_dir()
                matches = list(cache_dir.glob(f"{token}_*"))
                if matches:
                    file_path = matches[0]
                    _file_registry[token] = file_path
            except Exception:
                pass

        if file_path is None or not file_path.exists() or not file_path.is_file():
            logger.warning("File not found for token={}", token)
            return aiohttp_web.Response(text="Not found", status=404)

        mime, _ = mimetypes.guess_type(str(file_path))
        download_name = (
            request.rel_url.query.get("name")
            or "_".join(file_path.name.split("_")[1:])
            or file_path.name
        )
        ascii_name = "".join(ch if ord(ch) < 128 else "_" for ch in download_name) or "download"
        return aiohttp_web.FileResponse(file_path, headers={
            "Content-Type": mime or "application/octet-stream",
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(download_name)}"
            ),
        })

    async def _handle_list_conversations(self, request: Any) -> Any:
        """List all nanochat conversations by scanning session JSONL files."""
        from nanobot.config.paths import get_sessions_dir
        sessions_dir = get_sessions_dir()
        conversations = []
        try:
            for path in sessions_dir.glob("nanochat_*.jsonl"):
                try:
                    meta: dict[str, Any] = {}
                    last_message = ""
                    with open(path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            data = json.loads(line)
                            if data.get("_type") == "metadata":
                                meta = data
                                continue
                            if data.get("_ui_only"):
                                continue
                            role = data.get("role")
                            if role == "assistant" and data.get("content"):
                                last_message = (data["content"] or "")[:80]
                    key = meta.get("key", "")
                    if not key.startswith("nanochat:"):
                        continue
                    cid = key[len("nanochat:"):]
                    conversations.append({
                        "id": cid,
                        "last_message": last_message,
                        "_updated_at": meta.get("updated_at", ""),
                    })
                except Exception:
                    continue
        except Exception:
            pass

        conversations.sort(key=lambda c: c.pop("_updated_at", ""), reverse=True)
        return aiohttp_web.json_response({"conversations": conversations})

    async def _handle_new_conversation(self, request: Any) -> Any:
        data = await request.json() if request.body_exists else {}
        cid = data.get("id") or str(uuid.uuid4())[:8]
        return aiohttp_web.json_response({"conversation_id": cid})

    async def _handle_history(self, request: Any) -> Any:
        cid = request.match_info["conversation_id"]
        messages = self._read_session_messages(cid)
        return aiohttp_web.json_response({"history": self._session_to_ui(cid, messages)})

    async def _handle_delete_conversation(self, request: Any) -> Any:
        cid = request.match_info["conversation_id"]
        self._active_streams.discard(cid)
        return aiohttp_web.json_response({"ok": True})

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

        # Echo the user message to all connected WS clients (other tabs).
        await self._broadcast(cid, {
            "type": "content",
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
        self._active_streams.add(conversation_id)
        self._stream_segments[conversation_id] = []
        self._cur_round[conversation_id] = {
            # Keep the in-flight user message available for reconnect replay.
            # Slash commands are not rendered as user bubbles in the web UI.
            **(
                {"pending_user": {"content": content, "media": media}}
                if (content or media) and not content.startswith("/")
                else {}
            ),
        }
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

        # Replay history from the session JSONL.  Tag each entry with _replay
        # so the frontend knows to always append progress blocks (not coalesce them).
        session_msgs = self._read_session_messages(cid)
        for entry in self._session_to_ui(cid, session_msgs):
            try:
                await ws.send_str(json.dumps({**entry, "_replay": True}, ensure_ascii=False))
            except Exception:
                break

        # If a turn is still in-flight, replay the full mid-turn state so the
        # reconnecting client sees the same picture as clients that stayed connected.
        if cid in self._active_streams:
            cur = self._cur_round.get(cid, {})

            pending_user = cur.get("pending_user")
            if pending_user:
                try:
                    await ws.send_str(json.dumps({
                        "type": "content",
                        "role": "user",
                        "content": pending_user.get("content", ""),
                        "media": pending_user.get("media", []),
                        "conversation_id": cid,
                        "_replay": True,
                    }, ensure_ascii=False))
                except Exception:
                    pass

            # 1. Reset the live panel first (before any mid-turn segments arrive).
            try:
                await ws.send_str(json.dumps({
                    "type": "stream_start",
                    "conversation_id": cid,
                }, ensure_ascii=False))
            except Exception:
                pass

            # 2. Replay committed rounds in order: think_delta → stream_end → tool_call.
            #    The frontend handles these exactly as it would live events, so each
            #    round's thinking gets flushed into messagesByConv via stream_end and
            #    tool_call entries land in messagesByConv as well.
            for seg in self._stream_segments.get(cid, []):
                try:
                    await ws.send_str(json.dumps(seg, ensure_ascii=False))
                except Exception:
                    break

            # 3. Replay the current in-progress round's partial state in the
            # same order it originally streamed.
            for payload in self._cur_round_to_replay_payloads(cid, cur):
                try:
                    await ws.send_str(json.dumps(payload, ensure_ascii=False))
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
                    if msg_type == "content":
                        content = (data.get("content") or "").strip()
                        media = data.get("media") or []
                        if content or media:
                            # Echo to other subscribers
                            await self._broadcast(cid, {
                                "type": "content",
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
