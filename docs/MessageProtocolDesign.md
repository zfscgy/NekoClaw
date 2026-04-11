# Message Protocol Design

This document describes how messages flow through the system — from a user's input to the LLM, through tool execution, and back to the frontend — using a single unified data structure throughout.

---

## Core Abstraction: `StreamDelta`

Every piece of information in the system is represented as a `StreamDelta`:

```python
@dataclass
class StreamDelta:
    type: Literal["tool_call", "thinking", "content", "user", "tool_call_results", "system"]
    content: str | ToolCallRequest | list
```

`StreamDelta` is intentionally minimal. Its `type` field determines what `content` holds:

| `type`             | `content`                  | Meaning                                    |
|--------------------|----------------------------|--------------------------------------------|
| `system`           | `str`                      | System prompt (rebuilt each turn, never persisted) |
| `user`             | `str` or multimodal `list` | User's message                             |
| `thinking`         | `str`                      | LLM reasoning/scratchpad (display only)    |
| `content`          | `str`                      | LLM's text reply                           |
| `tool_call`        | `ToolCallRequest`          | A tool the LLM wants to invoke             |
| `tool_call_results`| `list[ToolCallResult]`     | Results after executing those tools        |

The entire conversation — history, in-progress turn, session file — is a flat `list[StreamDelta]`. This uniformity means the same structure is used for LLM context, streaming output, session persistence, and UI rendering.

---

## Layer-by-Layer Flow

```
User Input
    │
    ▼
[Channel] ──InboundMessage──► [Message Bus] ──► [Agent Loop]
                                                      │
                                          ┌───────────┴───────────┐
                                          │                       │
                                   [LLM Provider]          [Tool Execution]
                                          │                       │
                                    StreamDelta            ToolCallResult
                                          │                       │
                                          └───────────┬───────────┘
                                                      │
                                               OutboundMessage
                                                      │
                                    ◄─────────────────┘
[Channel] ◄──OutboundMessage── [Message Bus]
    │
    ▼ (WebSocket)
[Frontend]
```

---

## 1. Provider Layer

`LLMProvider` has two modes:

- **`chat()`** — blocking call; returns `list[StreamDelta]` once the full response is ready.
- **`chat_stream()`** — async generator; yields `StreamDelta` objects as tokens arrive.

The `OpenAIProvider` translates the raw OpenAI SDK streaming chunks into `StreamDelta` objects in real time:

- `delta.content` → `StreamDelta(type="content", content=text_chunk)`
- `delta.reasoning_content` → `StreamDelta(type="thinking", content=...)`
- `delta.tool_calls` → `StreamDelta(type="tool_call", content=ToolCallRequest(partial=True))` during streaming, then `partial=False` when complete

A `DeltaBuffer` sits between the raw stream and the caller. It merges consecutive same-type `thinking` and `content` chunks within a 1-second window, reducing the number of downstream events without losing any content.

Before calling the provider, the agent converts the internal `list[StreamDelta]` to OpenAI's wire format using `delta_to_openai()`. `thinking` deltas are silently dropped here — they are for display only and must not be fed back to the model.

---

## 2. Agent Loop

The agent loop is the engine that orchestrates everything. Its internal state is always a flat `list[StreamDelta]` representing the full conversation so far.

**Per-turn sequence:**

1. Load session history → `list[StreamDelta]`
2. Prepend system prompt + current user message → `initial_messages`
3. Convert to OpenAI format via `delta_to_openai(messages)` and call the provider
4. For each yielded `StreamDelta` from `chat_stream()`:
   - Forward it immediately to the bus via an `on_delta` callback (real-time streaming)
   - Accumulate `content`, `thinking`, and `tool_call` chunks locally
5. After the stream ends:
   - If tool calls were requested: execute each tool → collect `ToolCallResult` objects → append a `StreamDelta(type="tool_call_results")` → loop back to step 3
   - If no tool calls: append the final `content` delta and exit the loop
6. Save new deltas to the session JSONL file (skip `system`; strip runtime context prefix from `user`)

This cycle repeats up to `max_iterations` times, allowing the model to call tools in multiple rounds before producing a final text response.

---

## 3. Message Bus

The bus decouples channels from the agent. It carries two event types:

**`InboundMessage`** — user → agent:
```python
@dataclass
class InboundMessage:
    channel: str       # "nekochat", "telegram", etc.
    sender_id: str
    chat_id: str
    content: str
    media: list[str]
    metadata: dict
    session_key_override: str | None
```

**`OutboundMessage`** — agent → channel, with four signal types:

| `type`               | Meaning                                                  |
|----------------------|----------------------------------------------------------|
| `delta`              | Carries a live `StreamDelta` during generation           |
| `stream_start`       | A new turn is beginning                                  |
| `stream_end`         | The turn is finished; channels should flush/deliver      |
| `clear_unsent_buffer`| A tool call completed mid-turn; drop any pending buffers |

---

## 4. Channel Layer

`BaseChannel` provides two delivery strategies:

**Default (buffered):** Used by platform channels (Telegram, QQ, etc.). The `send()` method buffers all `content` delta strings and assembles them into a single `deliver()` call when `stream_end` arrives. The platform sees one complete message per turn.

**Streaming (nekochat):** `NekoChatChannel` overrides `send()` and pushes every delta to connected WebSocket clients the moment it arrives. It also maintains mid-turn replay state (`_stream_segments`, `_cur_round`) so a client that reconnects mid-generation receives the same picture as one that stayed connected the whole time.

---

## 5. WebSocket Protocol (NekoChat)

The nekochat channel translates `OutboundMessage` events into a JSON WebSocket protocol.

**Server → Client messages:**

| `type`        | Extra fields                    | Meaning                                 |
|---------------|---------------------------------|-----------------------------------------|
| `stream_start` | `conversation_id`              | Turn is beginning; reset live panel     |
| `thinking`    | `content`, `_delta: true`       | Streaming reasoning chunk               |
| `content`     | `role`, `content`, `_delta: true` | Streaming reply text chunk            |
| `tool_call`   | `content` (ToolCallPayload), `_delta: true` | Tool call (partial during streaming, complete when done) |
| `stream_end`  | `conversation_id`               | Turn complete; finalize display         |
| *(any)*       | `_replay: true`                 | History or mid-turn replay on reconnect |

**Client → Server messages** (sent over the WebSocket or fallback HTTP):

| `type`    | Fields              | Meaning                  |
|-----------|---------------------|--------------------------|
| `content` | `content`, `media`  | New user message         |
| `command` | `command`           | Slash command (e.g. `/new`) |

---

## 6. Frontend (`useChat.ts`)

The Vue composable processes WebSocket messages into a reactive `ChatMessage[]` array per conversation.

**`ChatMessage` types** (frontend-only):

| `type`              | Meaning                                   |
|---------------------|-------------------------------------------|
| `content`           | Plain text message (user or assistant)    |
| `think`             | Model reasoning/scratchpad block          |
| `tool_call`         | Tool invocation display                   |
| `reasoning_response`| (Reserved for future reasoning display)  |

**Streaming coalescing:** `_appendStreamText()` merges incoming `thinking` and `content` deltas into the last message of the same type rather than creating a new entry per chunk.

**Tool call tracking:** `_toolCallState` tracks in-progress tool calls by index and ID. Partial deltas update the display in-place; when `partial: false` arrives, the slot is finalized. A `send_message_with_attachments` tool call is transparently converted into a `content` message so the agent's outgoing message appears as a regular assistant bubble.

**`messageGroups` (computed):** Consecutive `tool_call` and `think` entries are grouped into collapsible `ActionsGroup` blocks; everything else becomes a flat `ContentGroup`. This lets the UI show an "actions" accordion around tool use without requiring any special session structure.

---

## 7. Session Persistence

Sessions are stored as JSONL files (`~/.nanobot/sessions/<channel>_<chat_id>.jsonl`). Each line is a `StreamDelta.to_dict()` snapshot:

```jsonl
{"type": "user", "content": "What is the weather like?"}
{"type": "thinking", "content": "The user is asking..."}
{"type": "tool_call", "content": {"index": 0, "id": "abc123", "name": "web_search", "arguments": {"query": "weather today"}}}
{"type": "tool_call_results", "content": [{"tool_call_id": "abc123", "name": "web_search", "content": "Sunny, 22°C"}]}
{"type": "content", "content": "It's sunny and 22°C today."}
```

`system` deltas are never written (the system prompt is rebuilt from config each turn). `thinking` deltas are written for display continuity but are stripped by `delta_to_openai()` before being sent to the LLM again — the model never sees its own prior reasoning.

When a session grows beyond `memory_window` messages, the agent asynchronously consolidates older history into a compressed memory summary to keep context windows manageable.

---

## Summary

The design has one key insight: **`StreamDelta` is the single shared language** across all layers. The provider produces it, the agent loop accumulates and routes it, the bus transports it, the channel delivers it, and the frontend renders it. The only transformations that happen are:

- `delta_to_openai()` — converts `list[StreamDelta]` to the wire format the LLM expects (inward boundary)
- `StreamDelta.to_dict()` / `from_dict()` — serializes for session storage (persistence boundary)
- `NekoChatChannel._session_to_ui()` — translates stored deltas to the frontend's JSON format (outward boundary for history replay)

Everything in between speaks `StreamDelta`.
