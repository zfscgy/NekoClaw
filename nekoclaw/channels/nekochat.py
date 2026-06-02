"""NekoChat web UI channel — serves a Vue.js chat frontend over HTTP + WebSocket."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from loguru import logger

from nekoclaw.bus.queue import MessageBus
from nekoclaw.bus.events import OutboundMessage
from nekoclaw.bus.events import InboundMessage
from nekoclaw.channels.base import BaseChannel
from nekoclaw.config.schema import NekoChatConfig


try:
    from aiohttp import web as aiohttp_web
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp_web = None  # type: ignore
    aiohttp = None  # type: ignore

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "nekochat" / "nekochat_frontend"
_FRONTEND_DIST = _FRONTEND_DIR / "dist"

# Token → cached path registry for serving files safely.
# Files are copied into a persistent cache dir at registration time so they
# remain downloadable even if the agent deletes or overwrites the originals.
_file_registry: dict[str, Path] = {}


def _get_file_cache_dir() -> Path:
    """Return (and create) the nekochat file-download cache directory."""
    from nekoclaw.config.paths import get_media_dir
    return get_media_dir("nekochat")


def _register_file(path: str) -> str:
    """Copy a file into the nekochat cache and return a stable URL token.

    The token is derived from the file content hash so that identical files
    share a cache entry, while re-generated files with new content get a
    fresh token and cache entry.
    """
    import hashlib
    src = Path(path).expanduser().resolve()
    if not src.exists() or not src.is_file():
        # Relative paths produced by the agent are relative to the workspace
        # root, not to the process CWD.  Try resolving against the workspace.
        if not Path(path).expanduser().is_absolute():
            from nekoclaw.config.paths import get_workspace_path
            candidate = get_workspace_path() / path
            if candidate.exists() and candidate.is_file():
                src = candidate
            else:
                raise FileNotFoundError(f"Cannot register non-existent file: {src}")
        else:
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


class NekoChatChannel(BaseChannel):
    """Chat web UI channel serving Vue.js frontend + REST + WebSocket API.

    Conversation history is stored in the standard agent session JSONL files
    (``<workspace>/sessions/nekochat_<cid>.jsonl``), which are the single source
    of truth for both the LLM context and the UI display.  Each entry is a
    serialized ``StreamDelta`` dict (type + content).  Thinking deltas are
    naturally excluded from LLM context by ``delta_to_openai``.
    """

    name = "nekochat"

    def __init__(self, config: NekoChatConfig, bus: MessageBus, workspace: Path):
        super().__init__(config, bus)
        self.config: NekoChatConfig = config
        self.workspace = workspace
        self.sessions_dir = workspace / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        # conversation_id -> set of active WebSocket connections
        self._ws_connections: dict[str, set] = {}
        # conversation_ids with an LLM turn currently in-flight
        self._active_streams: set[str] = set()
        # conversation_id -> ordered list of WS payload dicts representing the
        # in-flight turn's committed rounds (thinking/content/tool_call deltas).
        # Used to replay mid-turn history when a new WebSocket client reconnects
        # while generation is still running.
        self._stream_segments: dict[str, list[dict]] = {}
        # Per-conversation accumulation of the *current* in-progress LLM round
        # (reset each time a round commits via a complete tool_call).
        # Shape:
        # {
        #   "pending_user": {"content": str, "media": list[str]},
        #   "items": [
        #       {"kind": "thinking", "content": str},
        #       {"kind": "content", "content": str},
        #       {"kind": "tool_delta", "content": str},
        #   ],
        # }
        self._cur_round: dict[str, dict] = {}
        # Subagent tracking: conversation_id -> { subagent_id -> state }
        # state = { label, status, segments: [replay payloads], cur_round: {} }
        self._subagent_state: dict[str, dict[str, dict[str, Any]]] = {}
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
            "NekoChat web UI started at http://{}:{}", self.config.host, self.config.port
        )

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._runner:
            await self._runner.cleanup()
        logger.info("NekoChat web UI stopped")

    # ------------------------------------------------------------------
    # Session helpers — read & translate session JSONL to UI format
    # ------------------------------------------------------------------

    def _session_path(self, cid: str) -> Path:
        from nekoclaw.utils.helpers import safe_filename
        safe_key = safe_filename(f"nekochat_{cid}")
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _read_session_messages(self, cid: str) -> list[dict[str, Any]]:
        """Read raw messages from the session JSONL for this conversation."""
        path = self._session_path(cid)
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
        """Translate StreamDelta session entries into nekochat UI-format entries."""
        ui: list[dict[str, Any]] = []
        for m in messages:
            dtype = m.get("type")
            raw = m.get("content", "")

            if dtype == "user":
                text = raw
                if isinstance(raw, list):
                    # Filter out [image] placeholders — the real files are in the
                    # media field and will be shown as thumbnails / chips.
                    text = " ".join(
                        c.get("text", "") for c in raw
                        if isinstance(c, dict) and c.get("type") == "text"
                        and c.get("text") != "[image]"
                    ).strip()
                # Resolve stored filesystem paths → /file/{token} URLs for the UI.
                stored_media: list[str] = m.get("media") or []
                media_urls = NekoChatChannel._media_to_urls(stored_media) if stored_media else []
                if text or media_urls:
                    ui.append({
                        "type": "content", "role": "user",
                        "content": text, "media": media_urls,
                        "conversation_id": cid,
                    })

            elif dtype == "thinking":
                text = (raw or "").strip() if isinstance(raw, str) else ""
                if text:
                    ui.append({
                        "type": "think", "role": "assistant",
                        "content": text, "media": [],
                        "conversation_id": cid,
                    })

            elif dtype == "content":
                if raw:
                    entry: dict[str, Any] = {
                        "type": "content", "role": "assistant",
                        "content": raw, "media": [],
                        "conversation_id": cid,
                    }
                    if m.get("time"):
                        entry["time"] = m["time"]
                    ui.append(entry)

            elif dtype == "tool_call":
                tc = raw if isinstance(raw, dict) else {}
                name = tc.get("name", "tool")
                args = tc.get("arguments", {})
                tc_content: dict[str, Any] = {
                    "index": tc.get("index", 0),
                    "id": tc.get("id", ""),
                    "name": name,
                    "arguments": args,
                    "partial": bool(tc.get("partial", False)),
                }
                if name == "send_message_with_attachments":
                    try:
                        if isinstance(args, dict) and isinstance(args.get("media"), list):
                            tc_content["arguments"] = {
                                **args,
                                "media": NekoChatChannel._media_to_urls(args["media"]),
                            }
                    except Exception:
                        pass
                # Match live streaming deltas: content is ToolCallRequest-shaped JSON, not "name({...})" text.
                ui.append({
                    "type": "tool_call",
                    "role": "assistant",
                    "content": tc_content,
                    "media": [],
                    "conversation_id": cid,
                })

            elif dtype == "tool_call_results" and isinstance(raw, list):
                results: list[dict[str, Any]] = []
                for r in raw:
                    if isinstance(r, dict):
                        results.append({
                            "tool_call_id": r.get("tool_call_id", ""),
                            "name": r.get("name", ""),
                            "content": r.get("content", ""),
                        })
                if results:
                    ui.append({
                        "type": "tool_call_results",
                        "results": results,
                        "conversation_id": cid,
                    })

            elif dtype == "subagent_ref":
                ref = raw if isinstance(raw, dict) else {}
                ui.append({
                    "type": "subagent_ref",
                    "session_id": ref.get("session_id", ""),
                    "label": ref.get("label", ""),
                    "status": ref.get("status", "ok"),
                    "task": ref.get("task", ""),
                    "announce": ref.get("announce", ""),
                    "conversation_id": cid,
                })

            # tool_call_results: not shown in UI

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
        from nekoclaw.providers.base import ToolCallRequest, ToolCallResult

        conversation_id = msg.chat_id
        sub_id = msg.metadata.get("subagent_id")

        # ── Subagent messages ──────────────────────────────────────
        if sub_id:
            await self._send_subagent(msg, conversation_id, sub_id)
            return

        # ── Main-agent messages ────────────────────────────────────

        if msg.type == "clear_unsent_buffer":
            self._stream_segments.pop(conversation_id, None)
            self._cur_round.pop(conversation_id, None)
            return

        if msg.type == "stream_end":
            self._active_streams.discard(conversation_id)
            self._stream_segments.pop(conversation_id, None)
            self._cur_round.pop(conversation_id, None)
            await self._broadcast(conversation_id, {
                "type": "stream_end",
                "conversation_id": conversation_id,
                "time": datetime.now(timezone.utc).isoformat(),
            })
            return

        if msg.type != "delta" or msg.msg is None:
            return

        delta = msg.msg

        if delta.type == "thinking":
            cur = self._cur_round.setdefault(conversation_id, {})
            self._append_cur_round_text_item(cur, "thinking", delta.content)
            await self._broadcast(conversation_id, {
                "type": "thinking",
                "content": delta.content,
                "conversation_id": conversation_id,
                "_delta": True,
            })
            return

        if delta.type == "content" and isinstance(delta.content, str):
            cur = self._cur_round.setdefault(conversation_id, {})
            self._append_cur_round_text_item(cur, "content", delta.content)
            await self._broadcast(conversation_id, {
                "type": "content",
                "role": "assistant",
                "content": delta.content,
                "conversation_id": conversation_id,
                "_delta": True,
            })
            return

        if delta.type == "subagent_ref" and isinstance(delta.content, dict):
            ref = delta.content
            session_id = ref.get("session_id", "")
            # Drop the in-memory replay buffer for this subagent now that the
            # main agent has emitted (and persisted) the subagent_ref. Until
            # this point we keep the buffer alive so reconnecting clients can
            # still see a finished subagent's output even if the main agent
            # is busy with a long iteration and hasn't picked up the
            # announcement yet.
            if session_id.startswith("subagent:"):
                sid = session_id.split(":", 1)[1]
                subs = self._subagent_state.get(conversation_id)
                if subs is not None and sid in subs:
                    subs.pop(sid, None)
                    if not subs:
                        self._subagent_state.pop(conversation_id, None)
            await self._broadcast(conversation_id, {
                "type": "subagent_ref",
                "session_id": session_id,
                "label": ref.get("label", ""),
                "status": ref.get("status", "ok"),
                "task": ref.get("task", ""),
                "announce": ref.get("announce", ""),
                "conversation_id": conversation_id,
            })
            return

        if delta.type == "tool_call" and isinstance(delta.content, ToolCallRequest):
            tc = delta.content
            tc_dict: dict[str, Any] = {
                "index": tc.index, "id": tc.id, "name": tc.name,
                "arguments": tc.arguments, "partial": tc.partial,
            }

            if tc.partial:
                cur = self._cur_round.setdefault(conversation_id, {})
                self._append_cur_round_tool_delta_item(cur, tc_dict)

                broadcast_tc = dict(tc_dict)
                if isinstance(tc.arguments, dict) and tc.name == "send_message_with_attachments":
                    if isinstance(tc.arguments.get("media"), list):
                        broadcast_tc["arguments"] = {**tc.arguments, "media": self._media_to_urls(tc.arguments["media"])}

                await self._broadcast(conversation_id, {
                    "type": "tool_call",
                    "content": broadcast_tc,
                    "conversation_id": conversation_id,
                    "_delta": True,
                })
                return

            if isinstance(tc.arguments, dict) and tc.name == "send_message_with_attachments":
                if isinstance(tc.arguments.get("media"), list):
                    tc_dict["arguments"] = {**tc.arguments, "media": self._media_to_urls(tc.arguments["media"])}

            cur = self._cur_round.pop(conversation_id, {})
            segs = self._stream_segments.setdefault(conversation_id, [])
            segs.extend(self._cur_round_to_replay_payloads(conversation_id, cur))

            payload: dict[str, Any] = {
                "type": "tool_call",
                "content": tc_dict,
                "conversation_id": conversation_id,
                "_delta": True,
            }
            segs.append(payload)
            await self._broadcast(conversation_id, payload)
            return

        if delta.type == "tool_call_results" and isinstance(delta.content, list):
            results_payload = [
                {
                    "tool_call_id": r.tool_call_id,
                    "name": r.name,
                    "content": r.content,
                }
                for r in delta.content if isinstance(r, ToolCallResult)
            ]
            if not results_payload:
                return
            payload = {
                "type": "tool_call_results",
                "results": results_payload,
                "conversation_id": conversation_id,
            }
            segs = self._stream_segments.setdefault(conversation_id, [])
            segs.append(payload)
            await self._broadcast(conversation_id, payload)

    # ------------------------------------------------------------------
    # Subagent message handling
    # ------------------------------------------------------------------

    async def _send_subagent(self, msg: OutboundMessage, conversation_id: str, sub_id: str) -> None:
        """Route a subagent-tagged outbound message to WebSocket clients."""
        from nekoclaw.providers.base import ToolCallRequest, ToolCallResult

        label = msg.metadata.get("subagent_label", sub_id)
        subs = self._subagent_state.setdefault(conversation_id, {})

        if msg.type == "stream_start":
            subs[sub_id] = {
                "label": label,
                "status": "running",
                "segments": [],
                "cur_round": {},
            }
            await self._broadcast(conversation_id, {
                "type": "subagent_start",
                "subagent_id": sub_id,
                "label": label,
                "conversation_id": conversation_id,
            })
            return

        if msg.type == "stream_end":
            status = msg.metadata.get("subagent_status", "ok")
            session_id = f"subagent:{sub_id}"
            if sub_id in subs:
                subs[sub_id]["status"] = status
                subs[sub_id]["cur_round"] = {}
                subs[sub_id]["session_id"] = session_id
            await self._broadcast(conversation_id, {
                "type": "subagent_end",
                "subagent_id": sub_id,
                "status": status,
                "session_id": session_id,
                "conversation_id": conversation_id,
            })
            return

        if msg.type == "clear_unsent_buffer":
            state = subs.get(sub_id)
            if state:
                cur = state.pop("cur_round", {})
                state["segments"].extend(
                    self._cur_round_to_subagent_replay(conversation_id, sub_id, cur)
                )
                state["cur_round"] = {}
            return

        if msg.type != "delta" or msg.msg is None:
            return

        delta = msg.msg
        state = subs.get(sub_id)
        if not state:
            return

        if delta.type == "thinking":
            cur = state.setdefault("cur_round", {})
            self._append_cur_round_text_item(cur, "thinking", delta.content)
            await self._broadcast(conversation_id, {
                "type": "subagent_delta",
                "subagent_id": sub_id,
                "delta_type": "thinking",
                "content": delta.content,
                "conversation_id": conversation_id,
            })
            return

        if delta.type == "content" and isinstance(delta.content, str):
            cur = state.setdefault("cur_round", {})
            self._append_cur_round_text_item(cur, "content", delta.content)
            await self._broadcast(conversation_id, {
                "type": "subagent_delta",
                "subagent_id": sub_id,
                "delta_type": "content",
                "content": delta.content,
                "conversation_id": conversation_id,
            })
            return

        if delta.type == "tool_call" and isinstance(delta.content, ToolCallRequest):
            tc = delta.content
            tc_dict: dict[str, Any] = {
                "index": tc.index, "id": tc.id, "name": tc.name,
                "arguments": tc.arguments, "partial": tc.partial,
            }

            if tc.partial:
                cur = state.setdefault("cur_round", {})
                self._append_cur_round_tool_delta_item(cur, tc_dict)
                await self._broadcast(conversation_id, {
                    "type": "subagent_delta",
                    "subagent_id": sub_id,
                    "delta_type": "tool_call",
                    "content": tc_dict,
                    "conversation_id": conversation_id,
                })
                return

            # Complete tool call — commit round
            cur = state.pop("cur_round", {})
            state["segments"].extend(
                self._cur_round_to_subagent_replay(conversation_id, sub_id, cur)
            )
            payload: dict[str, Any] = {
                "type": "subagent_delta",
                "subagent_id": sub_id,
                "delta_type": "tool_call",
                "content": tc_dict,
                "conversation_id": conversation_id,
            }
            state["segments"].append(payload)
            state["cur_round"] = {}
            await self._broadcast(conversation_id, payload)
            return

        if delta.type == "tool_call_results" and isinstance(delta.content, list):
            results_payload = [
                {
                    "tool_call_id": r.tool_call_id,
                    "name": r.name,
                    "content": r.content,
                }
                for r in delta.content if isinstance(r, ToolCallResult)
            ]
            if not results_payload:
                return
            payload = {
                "type": "subagent_delta",
                "subagent_id": sub_id,
                "delta_type": "tool_call_results",
                "results": results_payload,
                "conversation_id": conversation_id,
            }
            state["segments"].append(payload)
            await self._broadcast(conversation_id, payload)

    @staticmethod
    def _cur_round_to_subagent_replay(
        conversation_id: str, sub_id: str, cur: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Convert a subagent's current round into replay payloads."""
        payloads: list[dict[str, Any]] = []
        for item in cur.get("items", []):
            kind = item.get("kind")
            if kind == "thinking" and item.get("content"):
                payloads.append({
                    "type": "subagent_delta",
                    "subagent_id": sub_id,
                    "delta_type": "thinking",
                    "content": item["content"],
                    "conversation_id": conversation_id,
                })
            elif kind == "content" and item.get("content"):
                payloads.append({
                    "type": "subagent_delta",
                    "subagent_id": sub_id,
                    "delta_type": "content",
                    "content": item["content"],
                    "conversation_id": conversation_id,
                })
            elif kind == "tool_delta" and item.get("content"):
                payloads.append({
                    "type": "subagent_delta",
                    "subagent_id": sub_id,
                    "delta_type": "tool_call",
                    "content": item["content"],
                    "conversation_id": conversation_id,
                })
        return payloads

    async def deliver(self, msg: OutboundMessage) -> None:
        """Not used — nekochat overrides send() for real-time streaming."""
        pass

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
    def _append_cur_round_tool_delta_item(cur: dict[str, Any], content: dict[str, Any]) -> None:
        """Append a tool-call delta dict in arrival order for reconnect replay."""
        if not content:
            return
        items = cur.setdefault("items", [])
        items.append({"kind": "tool_delta", "content": content})

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
                    "type": "thinking",
                    "content": item["content"],
                    "conversation_id": conversation_id,
                    "_delta": True,
                })
            elif kind == "content" and item.get("content"):
                payloads.append({
                    "type": "content",
                    "role": "assistant",
                    "content": item["content"],
                    "conversation_id": conversation_id,
                    "_delta": True,
                })
            elif kind == "tool_delta" and item.get("content"):
                payloads.append({
                    "type": "tool_call",
                    "content": item["content"],
                    "conversation_id": conversation_id,
                    "_delta": True,
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
        app.router.add_post("/api/conversations/{conversation_id}/stop", self._handle_stop_conversation)
        app.router.add_post("/api/conversations/{conversation_id}/message", self._handle_http_message)
        app.router.add_get("/api/subagent/{session_id}/history", self._handle_subagent_history)
        app.router.add_post("/api/upload", self._handle_upload)
        app.router.add_get("/file/{token}", self._handle_file)
        app.router.add_get("/assets/{path:.*}", self._handle_assets)
        # Runtime manager (generic config get/set + skills)
        app.router.add_get("/api/manager/config", self._handle_get_config)
        app.router.add_put("/api/manager/config", self._handle_set_config)
        app.router.add_get("/api/manager/skills", self._handle_list_skills)
        app.router.add_post("/api/manager/skills/upload", self._handle_upload_skill)
        app.router.add_post("/api/manager/skills/{name}/enable", self._handle_enable_skill)
        app.router.add_post("/api/manager/skills/{name}/disable", self._handle_disable_skill)

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _handle_index(self, request: Any) -> Any:
        html_path = _FRONTEND_DIST / "index.html" if _FRONTEND_DIST.exists() else _FRONTEND_DIR / "index.html"
        if html_path.exists():
            return aiohttp_web.FileResponse(html_path)
        return aiohttp_web.Response(text="NekoChat frontend not found.", status=404)

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

    async def _handle_upload(self, request: Any) -> Any:
        """Accept multipart file uploads, cache them, and return /file/{token} URLs."""
        import hashlib
        try:
            reader = await request.multipart()
        except Exception:
            return aiohttp_web.json_response({"error": "expected multipart/form-data"}, status=400)

        urls: list[str] = []
        cache_dir = _get_file_cache_dir()

        async for part in reader:
            filename = part.filename
            if not filename:
                continue
            try:
                data = await part.read()
                token = hashlib.sha1(data).hexdigest()[:16]
                safe_name = Path(filename).name
                cached = cache_dir / f"{token}_{safe_name}"
                if not cached.exists():
                    cached.write_bytes(data)
                    logger.debug("Upload cached: token={} name={}", token, safe_name)
                _file_registry[token] = cached
                urls.append(f"/file/{token}?name={quote(safe_name)}")
            except Exception as exc:
                logger.warning("Failed to cache uploaded file {}: {}", filename, exc)

        return aiohttp_web.json_response({"urls": urls})

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
        """List all nekochat conversations by scanning session JSONL files."""
        conversations = []
        try:
            for path in self.sessions_dir.glob("nekochat_*.jsonl"):
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
                            if data.get("type") == "content" and data.get("content"):
                                last_message = (data["content"] or "")[:80]
                    key = meta.get("key", "")
                    if not key.startswith("nekochat:"):
                        continue
                    cid = key[len("nekochat:"):]
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

    async def _handle_subagent_history(self, request: Any) -> Any:
        """Return the message history for a subagent session."""
        session_id = request.match_info["session_id"]
        messages = self._read_subagent_session_messages(session_id)
        ui = self._subagent_session_to_ui(messages)
        return aiohttp_web.json_response({"history": ui})

    def _read_subagent_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Read raw messages from a subagent session JSONL."""
        from nekoclaw.utils.helpers import safe_filename
        safe_key = safe_filename(session_id.replace(":", "_"))
        path = self.sessions_dir / f"{safe_key}.jsonl"
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
            logger.warning("Failed to read subagent session {}: {}", session_id, exc)
        return messages

    @staticmethod
    def _subagent_session_to_ui(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate subagent session entries into UI-format items for the card."""
        ui: list[dict[str, Any]] = []
        for m in messages:
            dtype = m.get("type")
            raw = m.get("content", "")
            if dtype == "thinking":
                text = (raw or "").strip() if isinstance(raw, str) else ""
                if text:
                    ui.append({"type": "think", "content": text})
            elif dtype == "content":
                if raw:
                    ui.append({"type": "content", "role": "assistant", "content": raw})
            elif dtype == "tool_call":
                tc = raw if isinstance(raw, dict) else {}
                name = tc.get("name", "tool")
                args = tc.get("arguments", {})
                tc_content: dict[str, Any] = {
                    "index": tc.get("index", 0),
                    "id": tc.get("id", ""),
                    "name": name,
                    "arguments": args,
                    "partial": bool(tc.get("partial", False)),
                }
                ui.append({"type": "tool_call", "content": tc_content})
            elif dtype == "tool_call_results" and isinstance(raw, list):
                results: list[dict[str, Any]] = []
                for r in raw:
                    if isinstance(r, dict):
                        results.append({
                            "tool_call_id": r.get("tool_call_id", ""),
                            "name": r.get("name", ""),
                            "content": r.get("content", ""),
                        })
                if results:
                    ui.append({"type": "tool_call_results", "results": results})
        return ui

    async def _handle_delete_conversation(self, request: Any) -> Any:
        cid = request.match_info["conversation_id"]
        if cid in self._active_streams:
            await self._request_stop(cid)
        self._active_streams.discard(cid)
        self._stream_segments.pop(cid, None)
        self._cur_round.pop(cid, None)
        self._subagent_state.pop(cid, None)

        from nekoclaw.manager import delete_session
        result = delete_session(f"{self.name}:{cid}")
        return aiohttp_web.json_response({"ok": True, **result})

    async def _handle_stop_conversation(self, request: Any) -> Any:
        cid = request.match_info["conversation_id"]
        await self._request_stop(cid)
        return aiohttp_web.json_response({"ok": True})

    async def _handle_http_message(self, request: Any) -> Any:
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

    def _resolve_user_media(self, media: list[str]) -> list[str]:
        """Resolve /file/{token} URLs to their cached filesystem paths.

        The agent's context builder expects local file paths so it can read
        and inline media.  Upload URLs produced by _handle_upload and the
        frontend are mapped back to their cache entries here before the
        InboundMessage is published.  External URLs are passed through unchanged.
        """
        resolved: list[str] = []
        for m in media:
            if m.startswith("/file/"):
                # Strip leading '/file/' and query string to get the token
                token = m[len("/file/"):].split("?")[0]
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
                if file_path and file_path.exists():
                    resolved.append(str(file_path))
                else:
                    logger.warning("Could not resolve media token={} — skipping", token)
            else:
                resolved.append(m)
        return resolved

    async def _handle_message_internal(
        self, conversation_id: str, content: str, media: list[str]
    ) -> None:
        self._active_streams.add(conversation_id)
        self._stream_segments[conversation_id] = []
        self._cur_round[conversation_id] = {
            **({"pending_user": {"content": content, "media": media}} if (content or media) else {}),
        }
        await self._broadcast(conversation_id, {
            "type": "stream_start",
            "conversation_id": conversation_id,
        })
        # Resolve /file/{token} URLs → real filesystem paths for the agent
        agent_media = self._resolve_user_media(media)
        await self._handle_message(
            sender_id="user",
            chat_id=conversation_id,
            content=content,
            media=agent_media,
            metadata={"conversation_id": conversation_id, "_streaming": True},
        )

    async def _request_stop(self, conversation_id: str) -> None:
        """Stop the in-flight turn immediately.

        Resolves the per-session :class:`AgentLoop` from the running dispatcher
        and calls its stop method directly so generation is interrupted
        mid-stream. When a turn is generating, its loop always exists, so there
        is nothing to stop if no loop is found.
        """
        from nekoclaw.config.manager import get_agent

        session_key = f"{self.name}:{conversation_id}"
        agent = get_agent()
        loop = agent.get_loop(session_key) if agent is not None else None
        if loop is not None:
            loop.request_stop()
        else:
            logger.debug("No active agent loop for {} — nothing to stop", session_key)

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

        # Replay every subagent that still has in-memory state. Entries are
        # only dropped once the main agent emits a subagent_ref (which is
        # persisted to the session JSONL just before the broadcast). So if a
        # subagent has finished but the main agent is still busy with a long
        # iteration and hasn't picked up the announcement, the buffer is
        # still here and we replay it — including a synthetic subagent_end so
        # the frontend marks the card complete instead of leaving it spinning.
        for sub_id, state in list(self._subagent_state.get(cid, {}).items()):
            status = state.get("status", "running")
            try:
                await ws.send_str(json.dumps({
                    "type": "subagent_start",
                    "subagent_id": sub_id,
                    "label": state.get("label", sub_id),
                    "conversation_id": cid,
                    "_replay": True,
                }, ensure_ascii=False))
            except Exception:
                break

            for seg in state.get("segments", []):
                try:
                    await ws.send_str(json.dumps(seg, ensure_ascii=False))
                except Exception:
                    break

            for payload in self._cur_round_to_subagent_replay(cid, sub_id, state.get("cur_round", {})):
                try:
                    await ws.send_str(json.dumps(payload, ensure_ascii=False))
                except Exception:
                    break

            if status != "running":
                session_id = state.get("session_id") or f"subagent:{sub_id}"
                try:
                    await ws.send_str(json.dumps({
                        "type": "subagent_end",
                        "subagent_id": sub_id,
                        "status": status,
                        "session_id": session_id,
                        "conversation_id": cid,
                        "_replay": True,
                    }, ensure_ascii=False))
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
                    elif msg_type == "stop":
                        await self._request_stop(cid)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        finally:
            self._ws_connections.get(cid, set()).discard(ws)
            logger.debug("WebSocket disconnected for conversation {}", cid)

        return ws

    # ------------------------------------------------------------------
    # Manager API — runtime config + skills
    # ------------------------------------------------------------------

    async def _handle_get_config(self, request: Any) -> Any:
        """Return the full runtime config dict and its JSON schema.

        ``?key=providers.openai.default.api_key`` returns only that value.
        """
        from nekoclaw.config.manager import to_dict as cfg_get
        from nekoclaw.config.manager import schema as cfg_schema

        key = request.rel_url.query.get("key")
        try:
            if key:
                return aiohttp_web.json_response({"key": key, "value": cfg_get(key)})
            return aiohttp_web.json_response({
                "config": cfg_get(),
                "schema": cfg_schema(),
            })
        except KeyError as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=404)
        except Exception as exc:
            logger.warning("Failed to read config: {}", exc)
            return aiohttp_web.json_response({"error": str(exc)}, status=500)

    async def _handle_set_config(self, request: Any) -> Any:
        """Apply a ``{key, value}`` mutation and return the refreshed config."""
        from nekoclaw.config.manager import to_dict as cfg_get
        from nekoclaw.config.manager import set_key as cfg_set

        try:
            body = await request.json()
        except Exception:
            return aiohttp_web.json_response({"error": "invalid JSON"}, status=400)

        if not isinstance(body, dict) or not isinstance(body.get("key"), str):
            return aiohttp_web.json_response(
                {"error": "expected object with a 'key' string"}, status=400
            )

        key = body["key"]
        value = body.get("value")
        try:
            cfg_set(key, value)
        except KeyError as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=404)
        except (ValueError, TypeError) as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.warning("Failed to set config {}: {}", key, exc)
            return aiohttp_web.json_response({"error": str(exc)}, status=500)

        return aiohttp_web.json_response({"key": key, "config": cfg_get()})

    async def _handle_list_skills(self, request: Any) -> Any:
        from nekoclaw.manager import list_skills
        try:
            return aiohttp_web.json_response({"skills": list_skills()})
        except Exception as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=500)

    async def _handle_enable_skill(self, request: Any) -> Any:
        from nekoclaw.manager import enable_skill
        name = request.match_info["name"]
        try:
            info = enable_skill(name)
        except FileNotFoundError as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=404)
        except (ValueError, FileExistsError) as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.warning("Failed to enable skill {}: {}", name, exc)
            return aiohttp_web.json_response({"error": str(exc)}, status=500)
        return aiohttp_web.json_response({"skill": info})

    async def _handle_disable_skill(self, request: Any) -> Any:
        from nekoclaw.manager import disable_skill
        name = request.match_info["name"]
        try:
            info = disable_skill(name)
        except FileNotFoundError as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.warning("Failed to disable skill {}: {}", name, exc)
            return aiohttp_web.json_response({"error": str(exc)}, status=500)
        return aiohttp_web.json_response({"skill": info})

    async def _handle_upload_skill(self, request: Any) -> Any:
        """Accept a zipped skill upload and install it to the workspace skills dir."""
        import tempfile
        from nekoclaw.manager import add_skill_from_zip

        try:
            reader = await request.multipart()
        except Exception:
            return aiohttp_web.json_response({"error": "expected multipart/form-data"}, status=400)

        tmp_path: Path | None = None
        override_name: str | None = None
        async for part in reader:
            if part.name == "name":
                try:
                    override_name = (await part.text()).strip() or None
                except Exception:
                    override_name = None
                continue
            filename = part.filename
            if not filename:
                continue
            if not filename.lower().endswith(".zip"):
                return aiohttp_web.json_response(
                    {"error": "skill upload must be a .zip archive"}, status=400
                )
            tmp_fd = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            try:
                while True:
                    chunk = await part.read_chunk(64 * 1024)
                    if not chunk:
                        break
                    tmp_fd.write(chunk)
            finally:
                tmp_fd.close()
            tmp_path = Path(tmp_fd.name)
            break

        if tmp_path is None:
            return aiohttp_web.json_response({"error": "no file uploaded"}, status=400)

        try:
            info = add_skill_from_zip(tmp_path, name=override_name)
        except FileExistsError as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=409)
        except (ValueError, FileNotFoundError) as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.warning("Failed to install uploaded skill: {}", exc)
            return aiohttp_web.json_response({"error": str(exc)}, status=500)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        return aiohttp_web.json_response({"skill": info})

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
