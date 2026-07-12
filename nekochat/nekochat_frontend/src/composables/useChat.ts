import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { isSubagentToolCall } from '../utils/actions'

// ── Types ──────────────────────────────────────────────────────────

type WsStatusKey = 'connected' | 'connecting' | 'disconnected'

const WS_STATUS: Record<WsStatusKey, string> = {
  connected: 'Connected',
  connecting: 'Connecting…',
  disconnected: 'Disconnected',
}

export interface Conversation {
  id: string
  preview: string
}

export interface ChatMessage {
  type: 'content' | 'think' | 'tool_call' | 'tool_call_results' | 'reasoning_response' | 'subagent_status'
  role?: string
  content?: string
  results?: ToolCallResultPayload[]
  media?: string[]
  conversation_id?: string
  _replay?: boolean
  time?: string
  // ``providerName:modelId`` tag of the model that produced this assistant
  // delta. Present on assistant-generated entries (content/think/tool_call).
  model?: string
  // Only meaningful for type === 'tool_call': the provider tool_call id,
  // used to match against tool_call_results for click-to-reveal display.
  toolCallId?: string
  toolName?: string
  toolResult?: string
  // Raw arguments dict for tool_call, used to render the args panel.
  toolArguments?: Record<string, unknown>
  subagentId?: string
  subagentSessionId?: string
  subagentLabel?: string
  subagentEvent?: 'started' | 'finished'
  subagentStatus?: 'running' | 'ok' | 'error'
  subagentReport?: string
  subagentTask?: string
}

interface ToolCallSlot {
  name: string
  arguments: string
  arrIdx: number
  toolCallId: string | null
}

interface ToolCallState {
  current: ToolCallSlot | null
}

export type StreamStatus = 'generating' | 'complete'

export interface ContentGroup {
  type: 'content'
  role?: string
  content?: string
  media: string[]
  appendCursor?: boolean
  streamStatus?: StreamStatus
  time?: string
  model?: string
}

export interface ActionsGroup {
  type: 'actions'
  items: ChatMessage[]
  key: string
  appendCursor?: boolean
  streamStatus?: StreamStatus
}

export interface SubagentStatusGroup {
  type: 'subagent_status'
  subagentId?: string
  subagentSessionId?: string
  label: string
  event: 'started' | 'finished'
  status: 'running' | 'ok' | 'error'
  report?: string
  task?: string
  appendCursor?: boolean
  streamStatus?: StreamStatus
}

export type MessageGroup = ContentGroup | ActionsGroup | SubagentStatusGroup

// Tool call payload from the backend (matches ToolCallRequest fields)
interface ToolCallPayload {
  index: number
  id: string
  name: string
  arguments: Record<string, unknown>
  partial: boolean
}

interface ToolCallResultPayload {
  tool_call_id: string
  name: string
  content: string
}

// Discriminated union for incoming WebSocket messages.
// Streaming deltas carry `_delta: true`; session-replay entries carry `_replay: true`.
type WsMessage =
  | { type: 'stream_start'; conversation_id?: string }
  | { type: 'stream_end'; conversation_id?: string; time?: string }
  | { type: 'thinking'; content?: string; model?: string; conversation_id?: string; _delta?: boolean }
  | { type: 'content'; role?: string; content?: string; model?: string; media?: string[]; conversation_id?: string; _replay?: boolean; _delta?: boolean; time?: string }
  | { type: 'think'; role?: string; content?: string; model?: string; media?: string[]; conversation_id?: string; _replay?: boolean }
  | { type: 'tool_call'; role?: string; content?: string | ToolCallPayload; model?: string; media?: string[]; conversation_id?: string; _replay?: boolean; _delta?: boolean; tool_call_id?: string; tool_name?: string }
  | { type: 'tool_call_results'; results: ToolCallResultPayload[]; conversation_id?: string; _replay?: boolean }
  | { type: 'subagent_start'; subagent_id: string; label: string; conversation_id?: string; _replay?: boolean }
  | { type: 'subagent_delta'; subagent_id: string; delta_type: 'thinking' | 'content' | 'tool_call' | 'tool_call_results'; content?: string | ToolCallPayload; results?: ToolCallResultPayload[]; model?: string; conversation_id?: string }
  | { type: 'subagent_end'; subagent_id: string; status: string; conversation_id?: string }
  | { type: 'subagent_ref'; session_id: string; label: string; status: string; task?: string; announce?: string; conversation_id?: string; _replay?: boolean }

export interface SubagentState {
  id: string
  label: string
  status: 'running' | 'ok' | 'error'
  items: ChatMessage[]
  sessionId?: string
}

// ── Private helpers ────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function _safeJsonParse(value: string): any {
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

function _stringifyToolArguments(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function _formatToolCall(item: Pick<ToolCallSlot, 'name' | 'arguments'>): string {
  return `${item.name || 'tool'}(${item.arguments || ''})`
}

function _extractArgsFromContent(content: string): Record<string, unknown> | undefined {
  const open = content.indexOf('(')
  if (open < 0) return undefined
  const close = content.lastIndexOf(')')
  if (close <= open) return undefined
  const inner = content.slice(open + 1, close).trim()
  if (!inner) return undefined
  const parsed = _safeJsonParse(inner)
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed))
    return parsed as Record<string, unknown>
  return undefined
}

function _toolCallPayloadToChatMessage(
  tc: string | ToolCallPayload | undefined,
  cid: string,
  extras?: { tool_call_id?: string; tool_name?: string; model?: string },
): ChatMessage | null {
  if (!tc) return null
  const model = extras?.model

  // Legacy: string content from session replay
  if (typeof tc === 'string') {
    if (tc.startsWith('send_message_with_attachments(')) {
      const argsStr = tc.slice('send_message_with_attachments('.length, tc.endsWith(')') ? -1 : undefined)
      const args = _safeJsonParse(argsStr)
      if (args && typeof args === 'object')
        return { type: 'content', role: 'assistant', content: args.content || '', media: Array.isArray(args.media) ? args.media : [], conversation_id: cid, model }
    }
    const name = extras?.tool_name || tc.split('(', 1)[0]
    return {
      type: 'tool_call', content: tc, conversation_id: cid,
      toolCallId: extras?.tool_call_id, toolName: name,
      toolArguments: _extractArgsFromContent(tc),
      model,
    }
  }

  // Structured ToolCallPayload from backend
  if (tc.name === 'send_message_with_attachments' && !tc.partial) {
    const args = tc.arguments as Record<string, unknown>
    if (args && typeof args === 'object')
      return { type: 'content', role: 'assistant', content: (args.content as string) || '', media: Array.isArray(args.media) ? args.media as string[] : [], conversation_id: cid, model }
  }

  const argsDict = (tc.arguments && typeof tc.arguments === 'object' && !Array.isArray(tc.arguments))
    ? (tc.arguments as Record<string, unknown>)
    : undefined
  return {
    type: 'tool_call',
    content: _formatToolCall({ name: tc.name || '', arguments: _stringifyToolArguments(tc.arguments) }),
    conversation_id: cid,
    toolCallId: tc.id || extras?.tool_call_id,
    toolName: tc.name || extras?.tool_name,
    toolArguments: argsDict,
    model,
  }
}

/** Extract a `subagent:<id>` session key from a spawn tool's result string. */
function _extractSpawnSessionId(result: string): string | null {
  if (!result) return null
  const m = result.match(/session_id:\s*(subagent:[A-Za-z0-9_-]+)/i)
  return m ? m[1] : null
}

/** Extract the subagent display label from a spawn tool's result string. */
function _extractSpawnLabel(result: string): string | null {
  if (!result) return null
  const m = result.match(/Subagent\s+\[([^\]]+)\]/)
  return m ? m[1] : null
}

function _normalizeSubagentStatus(status: string | undefined): 'ok' | 'error' {
  return status === 'error' ? 'error' : 'ok'
}

function _subagentLabelFromTool(entry: ChatMessage | null, fallback: string): string {
  const args = entry?.toolArguments
  const label = args?.label
  const task = args?.task
  if (typeof label === 'string' && label.trim()) return label.trim()
  if (typeof task === 'string' && task.trim())
    return task.length > 30 ? `${task.slice(0, 30)}...` : task
  return fallback
}

function _isSubagentToolResult(result: ToolCallResultPayload): boolean {
  return result.name === 'call_subagent' || result.name === 'spawn' || !!_extractSpawnSessionId(result.content)
}

function _pushSubagentStatus(
  arr: ChatMessage[],
  cid: string,
  item: {
    id?: string
    sessionId?: string
    label: string
    event: 'started' | 'finished'
    status: 'running' | 'ok' | 'error'
    report?: string
    task?: string
  },
): void {
  const exists = arr.some(m =>
    m.type === 'subagent_status' &&
    m.subagentEvent === item.event &&
    (
      (item.sessionId && m.subagentSessionId === item.sessionId) ||
      (item.id && m.subagentId === item.id)
    )
  )
  if (exists) return

  arr.push({
    type: 'subagent_status',
    conversation_id: cid,
    subagentId: item.id,
    subagentSessionId: item.sessionId,
    subagentLabel: item.label,
    subagentEvent: item.event,
    subagentStatus: item.status,
    subagentReport: item.report,
    subagentTask: item.task,
  })
}

function _tryConvertMessageToolCall(entry: ChatMessage, cid: string): ChatMessage | null {
  if (entry.type !== 'tool_call') return null
  const raw = entry.content || ''
  if (!raw.startsWith('send_message_with_attachments(')) return null
  const argsStr = raw.slice('send_message_with_attachments('.length, raw.endsWith(')') ? -1 : undefined)
  const args = _safeJsonParse(argsStr)
  if (!args || typeof args !== 'object') return null
  return {
    type: 'content',
    role: 'assistant',
    content: args.content || '',
    media: Array.isArray(args.media) ? args.media : [],
    conversation_id: cid,
    model: entry.model,
    time: entry.time,
  }
}

/** Indices of the last assistant content in each model turn (between user messages). */
function _terminalAssistantContentIndices(msgs: ChatMessage[]): Set<number> {
  const terminals = new Set<number>()
  let turnStart = 0

  for (let i = 0; i <= msgs.length; i++) {
    const atEnd = i === msgs.length
    const isUserBoundary = !atEnd && msgs[i].type === 'content' && msgs[i].role === 'user'

    if (atEnd || isUserBoundary) {
      for (let j = i - 1; j >= turnStart; j--) {
        const m = msgs[j]
        if (m.type === 'content' && m.role === 'assistant') {
          terminals.add(j)
          break
        }
      }
      if (!atEnd) turnStart = i + 1
    }
  }
  return terminals
}

// ── Composable ─────────────────────────────────────────────────────

export function useChat() {
  const conversations = ref<Conversation[]>([])
  const activeId = ref<string | null>(null)
  const messagesByConv = ref<Record<string, ChatMessage[]>>({})
  const inputText = ref('')
  const isTyping = ref(false)
  const isStreaming = ref(false)
  const streamActive = ref(false)
  const streamDone = ref(false)
  const wsStatus = ref<WsStatusKey>('disconnected')
  const lightboxSrc = ref<string | null>(null)
  const messagesEl = ref<HTMLElement | null>(null)
  const groupOpenState = ref<Record<string, boolean>>({})
  const subagents = ref<Record<string, SubagentState[]>>({})

  const currentSubagents = computed<SubagentState[]>(() =>
    activeId.value ? (subagents.value[activeId.value] || []) : []
  )

  const wsStatusLabel = computed(() => WS_STATUS[wsStatus.value])

  const currentMessages = computed<ChatMessage[]>(() =>
    activeId.value ? (messagesByConv.value[activeId.value] || []) : []
  )

  let ws: WebSocket | null = null
  let wsReconnectTimer: number | undefined
  let wsGeneration = 0
  const _openCache: Record<string, boolean> = {}

  let _toolCallState: ToolCallState = { current: null }
  let _roundStart = 0

  // ── Streaming batch buffer ────────────────────────────────────────
  // High-frequency stream deltas (token-by-token text, tool-call argument
  // chunks, subagent deltas) are aggregated into a non-reactive queue and
  // flushed periodically. Without this, each delta triggers a full
  // markdown + DOMPurify + KaTeX re-render of the streaming bubble, which
  // becomes the dominant cost when the model emits dozens of tokens per
  // second. Flushing at `STREAM_FLUSH_INTERVAL_MS` caps the markdown work
  // at ~(1000 / interval) renders/sec while still feeling live. Tune via
  // the constant below — e.g. set to 1000 for the most aggressive batching.
  const STREAM_FLUSH_INTERVAL_MS = 200

  interface BufferedDelta {
    cid: string
    kind:
      | 'thinking'
      | 'content'
      | 'tool_call'
      | 'subagent_thinking'
      | 'subagent_content'
      | 'subagent_tool_call'
      | 'subagent_tool_call_results'
    text?: string
    role?: string
    tc?: ToolCallPayload
    results?: ToolCallResultPayload[]
    subagentId?: string
    model?: string
  }

  const _streamBuffer: BufferedDelta[] = []
  let _flushTimer: number | undefined
  // Keys that the user explicitly opened via the toggle UI (not auto-opened).
  // Auto-close logic skips these so user-opened panels stay open.
  const _userOpenedKeys = new Set<string>()

  // ── Scroll state ──────────────────────────────────────────────────
  // Tolerance (px) for "near bottom" detection. Anything within this
  // distance of the bottom is considered pinned, so streaming reflows
  // (late image loads, code highlighting, etc.) can keep us at the
  // bottom without yanking the user back if they've intentionally
  // scrolled up to read earlier content.
  const SCROLL_BOTTOM_TOLERANCE = 80
  let _userScrolledUp = false
  let _resizeObserver: ResizeObserver | null = null
  let _observedInner: HTMLElement | null = null
  let _scrollHandler: ((e: Event) => void) | null = null

  function setGroupOpen(key: string, val: boolean): void {
    _openCache[key] = val
    if (val) {
      _userOpenedKeys.add(key)
    } else {
      _userOpenedKeys.delete(key)
    }
    groupOpenState.value = { ...groupOpenState.value, [key]: val }
  }

  const messageGroups = computed<MessageGroup[]>(() => {
    const msgs = currentMessages.value
    const terminals = _terminalAssistantContentIndices(msgs)
    const groups: MessageGroup[] = []
    const pendingOpen: Record<string, boolean> = {}
    let i = 0

    while (i < msgs.length) {
      const m = msgs[i]

      if (m.type === 'tool_call' && isSubagentToolCall(m)) {
        i++
        continue
      }

      if (m.type === 'subagent_status') {
        groups.push({
          type: 'subagent_status',
          subagentId: m.subagentId,
          subagentSessionId: m.subagentSessionId,
          label: m.subagentLabel || m.subagentSessionId || m.subagentId || 'subagent',
          event: m.subagentEvent || 'finished',
          status: m.subagentStatus || 'ok',
          report: m.subagentReport,
          task: m.subagentTask,
        })
        i++
        continue
      }

      if (m.type === 'tool_call' || m.type === 'think' || m.type === 'reasoning_response') {
        const items: ChatMessage[] = []
        while (
          i < msgs.length &&
          (msgs[i].type === 'tool_call' || msgs[i].type === 'think' || msgs[i].type === 'reasoning_response') &&
          !(msgs[i].type === 'tool_call' && isSubagentToolCall(msgs[i]))
        ) {
          items.push(msgs[i])
          i++
        }
        if (items.length) {
          const isLive = i >= msgs.length
          const key = 'ag-' + groups.length
          if (_openCache[key] === undefined) pendingOpen[key] = isLive
          else if (isLive && !_openCache[key]) pendingOpen[key] = true
          groups.push({ type: 'actions', items, key })
        }
      } else {
        if (groups.length) {
          const prev = groups[groups.length - 1]
          if (prev.type === 'actions' && _openCache[prev.key] !== false && !_userOpenedKeys.has(prev.key)) pendingOpen[prev.key] = false
        }
        groups.push({
          type: 'content',
          role: m.role,
          content: m.content,
          media: m.media || [],
          time: terminals.has(i) ? m.time : undefined,
          model: terminals.has(i) ? m.model : undefined,
        })
        i++
      }
    }

    if (isStreaming.value && groups.length) {
      groups[groups.length - 1] = { ...groups[groups.length - 1], appendCursor: true }
    }

    const streamStatus: StreamStatus | undefined = streamActive.value
      ? 'generating'
      : streamDone.value
        ? 'complete'
        : undefined
    if (streamStatus && groups.length) {
      groups[groups.length - 1] = { ...groups[groups.length - 1], streamStatus }
    }

    if (Object.keys(pendingOpen).length) {
      Promise.resolve().then(() => {
        let changed = false
        for (const [k, v] of Object.entries(pendingOpen)) {
          if (_openCache[k] !== v) { _openCache[k] = v; changed = true }
        }
        if (changed) groupOpenState.value = { ...groupOpenState.value, ...pendingOpen }
      })
    }
    return groups
  })

  // ── Streaming helpers ──────────────────────────────────────────────

  function _appendStreamText(arr: ChatMessage[], type: ChatMessage['type'], role: string | undefined, text: string, model?: string): void {
    if (!text) return
    const last = arr.length ? arr[arr.length - 1] : null
    if (last && last.type === type && (!role || last.role === role)) {
      arr[arr.length - 1] = { ...last, content: (last.content || '') + text, model: model || last.model }
    } else {
      const entry: ChatMessage = { type, content: text }
      if (role) entry.role = role
      if (model) entry.model = model
      arr.push(entry)
    }
  }

  function _handleToolCallDelta(arr: ChatMessage[], cid: string, tc: ToolCallPayload, model?: string): void {
    let current = _toolCallState.current
    const incomingId = tc.id || null

    const needNewSlot =
      !current ||
      (incomingId != null && current.toolCallId != null && incomingId !== current.toolCallId)

    if (needNewSlot) {
      current = { name: '', arguments: '', arrIdx: arr.length, toolCallId: incomingId }
      _toolCallState.current = current
      arr.push({
        type: 'tool_call',
        content: _formatToolCall(current),
        conversation_id: cid,
        toolCallId: incomingId || undefined,
        toolName: tc.name || undefined,
        model,
      })
    }

    if (tc.name) current!.name = tc.name
    const hasArgs = tc.arguments && Object.keys(tc.arguments).length > 0
    if (hasArgs) current!.arguments = _stringifyToolArguments(tc.arguments)
    if (incomingId != null) current!.toolCallId = incomingId
    const argsDict = (tc.arguments && typeof tc.arguments === 'object' && !Array.isArray(tc.arguments))
      ? (tc.arguments as Record<string, unknown>)
      : arr[current!.arrIdx].toolArguments
    arr[current!.arrIdx] = {
      ...arr[current!.arrIdx],
      content: _formatToolCall(current!),
      toolCallId: current!.toolCallId || arr[current!.arrIdx].toolCallId,
      toolName: current!.name || arr[current!.arrIdx].toolName,
      toolArguments: argsDict,
      model: model || arr[current!.arrIdx].model,
    }

    if (!tc.partial) {
      if (tc.name === 'send_message_with_attachments') {
        const args = tc.arguments as Record<string, unknown>
        if (args && typeof args === 'object') {
          arr[current!.arrIdx] = {
            type: 'content',
            role: 'assistant',
            content: (args.content as string) || '',
            media: Array.isArray(args.media) ? args.media as string[] : [],
            conversation_id: cid,
            model,
          }
        }
      }
      _toolCallState.current = null
    }
  }

  /**
   * Attach a tool-call result to its matching tool_call entry so the UI can
   * reveal it on click. For spawn results, also create/update the subagent card.
   */
  function _applyToolCallResults(arr: ChatMessage[], cid: string, results: ToolCallResultPayload[]): void {
    for (const r of results) {
      let matchedEntry: ChatMessage | null = null
      if (r.tool_call_id) {
        for (let i = arr.length - 1; i >= 0; i--) {
          const entry = arr[i]
          if (entry.type === 'tool_call' && entry.toolCallId === r.tool_call_id) {
            arr[i] = { ...entry, toolResult: r.content, toolName: entry.toolName || r.name }
            matchedEntry = arr[i]
            break
          }
        }
      }

      if (_isSubagentToolResult(r)) {
        const sessionId = _extractSpawnSessionId(r.content)
        const plainId = sessionId?.replace(/^subagent:/, '') || r.tool_call_id
        if (plainId) {
          const label = _extractSpawnLabel(r.content) || _subagentLabelFromTool(matchedEntry, plainId)
          _pushSubagentStatus(arr, cid, {
            id: plainId,
            sessionId: sessionId || undefined,
            label,
            event: 'started',
            status: 'running',
            task: typeof matchedEntry?.toolArguments?.task === 'string' ? matchedEntry.toolArguments.task : undefined,
          })
        }

        if (sessionId) {
          if (!subagents.value[cid]) subagents.value[cid] = []
          const plainId = sessionId.replace(/^subagent:/, '')
          const existing = subagents.value[cid].find(s =>
            s.id === plainId || s.sessionId === sessionId
          )
          if (!existing) {
            subagents.value[cid].push({
              id: plainId,
              label: _extractSpawnLabel(r.content) || _subagentLabelFromTool(matchedEntry, plainId),
              status: 'running',
              items: [],
              sessionId,
            })
            subagents.value = { ...subagents.value }
          } else if (!existing.sessionId) {
            existing.sessionId = sessionId
            subagents.value = { ...subagents.value }
          }
        }
      }
    }
  }

  function _applySubagentHistoryResults(items: ChatMessage[], cid: string): ChatMessage[] {
    const hydrated: ChatMessage[] = []
    for (const entry of items) {
      if (entry.type === 'tool_call') {
        const converted = _toolCallPayloadToChatMessage(entry.content, cid, {
          tool_call_id: entry.toolCallId,
          tool_name: entry.toolName,
          model: entry.model,
        })
        hydrated.push(converted || entry)
      } else if (entry.type === 'tool_call_results') {
        const results = (entry as unknown as { results?: ToolCallResultPayload[] }).results || []
        if (results.length) _applyToolCallResults(hydrated, cid, results)
      } else {
        hydrated.push(entry)
      }
    }
    return hydrated
  }

  function _handleSubagentToolCallDelta(sub: SubagentState, cid: string, tc: ToolCallPayload, model?: string): void {
    const chatMsg = _toolCallPayloadToChatMessage(tc, cid, { model })
    if (!chatMsg) return
    const lastIdx = sub.items.length - 1
    const last = lastIdx >= 0 ? sub.items[lastIdx] : null

    if (tc.partial) {
      if (last?.type === 'tool_call' && (!tc.id || !last.toolCallId || last.toolCallId === tc.id)) {
        sub.items[lastIdx] = { ...last, ...chatMsg }
      } else {
        sub.items.push(chatMsg)
      }
      return
    }

    if (
      last?.type === 'tool_call' &&
      !last.toolResult &&
      (
        (chatMsg.toolCallId && last.toolCallId === chatMsg.toolCallId) ||
        (!last.toolCallId && last.toolName === chatMsg.toolName)
      )
    ) {
      sub.items[lastIdx] = { ...last, ...chatMsg }
      return
    }

    sub.items.push(chatMsg)
  }

  function _updatePreview(cid: string, content: string | undefined): void {
    const c = conversations.value.find(c => c.id === cid)
    if (c) c.preview = (content || '').slice(0, 80)
  }

  // ── Subagent history fetch ────────────────────────────────────────

  async function _fetchSubagentHistory(sessionId: string, cid: string): Promise<void> {
    try {
      const res = await fetch(`/api/subagent/${encodeURIComponent(sessionId)}/history`)
      if (!res.ok) return
      const data = await res.json() as { history?: ChatMessage[] }
      const plainId = sessionId.replace(/^subagent:/, '')
      const subs = subagents.value[cid] || []
      const sub = subs.find(s => s.sessionId === sessionId || s.id === plainId)
      if (sub && data.history) {
        sub.items = _applySubagentHistoryResults(data.history, cid)
        sub.sessionId = sessionId
        subagents.value = { ...subagents.value }
      }
    } catch {
      // ignore fetch errors
    }
  }

  // ── Stream buffer flush ───────────────────────────────────────────

  function _scheduleFlush(): void {
    if (_flushTimer !== undefined) return
    _flushTimer = window.setTimeout(() => {
      _flushTimer = undefined
      _flushStreamBuffer()
    }, STREAM_FLUSH_INTERVAL_MS)
  }

  // Drain the buffered deltas in arrival order and apply them to the
  // reactive arrays in a single batch. Vue collects every mutation in
  // this synchronous block into one render pass, so `messageGroups` and
  // the streaming `MessageBubble.rendered` computed run at most once
  // per flush regardless of how many deltas were queued.
  function _flushStreamBuffer(): void {
    if (_flushTimer !== undefined) {
      clearTimeout(_flushTimer)
      _flushTimer = undefined
    }
    if (!_streamBuffer.length) return

    let subagentsDirty = false
    for (const d of _streamBuffer) {
      if (
        d.kind === 'thinking' ||
        d.kind === 'content' ||
        d.kind === 'tool_call'
      ) {
        if (!messagesByConv.value[d.cid]) messagesByConv.value[d.cid] = []
        const arr = messagesByConv.value[d.cid]
        if (d.kind === 'thinking') {
          _appendStreamText(arr, 'think', undefined, d.text || '', d.model)
        } else if (d.kind === 'content') {
          _appendStreamText(arr, 'content', d.role || 'assistant', d.text || '', d.model)
        } else if (d.tc) {
          _handleToolCallDelta(arr, d.cid, d.tc, d.model)
        }
      } else if (d.subagentId) {
        const subs = subagents.value[d.cid] || []
        const sub = subs.find(s => s.id === d.subagentId)
        if (!sub) continue
        if (d.kind === 'subagent_thinking') {
          _appendStreamText(sub.items, 'think', undefined, d.text || '', d.model)
        } else if (d.kind === 'subagent_content') {
          _appendStreamText(sub.items, 'content', 'assistant', d.text || '', d.model)
        } else if (d.kind === 'subagent_tool_call' && d.tc) {
          _handleSubagentToolCallDelta(sub, d.cid, d.tc, d.model)
        } else if (d.kind === 'subagent_tool_call_results' && d.results) {
          _applyToolCallResults(sub.items, d.cid, d.results)
        }
        subagentsDirty = true
      }
    }
    _streamBuffer.length = 0
    if (subagentsDirty) {
      subagents.value = { ...subagents.value }
    }
    scrollToBottom()
  }

  function _clearStreamBuffer(): void {
    _streamBuffer.length = 0
    if (_flushTimer !== undefined) {
      clearTimeout(_flushTimer)
      _flushTimer = undefined
    }
  }

  // ── WebSocket ──────────────────────────────────────────────────────

  function _closeWs(): void {
    wsGeneration++
    clearTimeout(wsReconnectTimer)
    _clearStreamBuffer()
    if (ws) {
      try { ws.close() } catch (_) {}
      ws = null
    }
    wsStatus.value = 'disconnected'
    isStreaming.value = false
  }

  function connectWs(convId: string): void {
    _closeWs()
    if (!convId) return
    const myGen = wsGeneration
    wsStatus.value = 'connecting'
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/ws/${convId}`)

    ws.onopen = () => {
      if (wsGeneration !== myGen) return
      wsStatus.value = 'connected'
    }

    ws.onmessage = (ev: MessageEvent) => {
      if (wsGeneration !== myGen) return
      const msg = JSON.parse(ev.data as string) as WsMessage
      const cid = msg.conversation_id || convId

      // ── Fast path: queue high-frequency stream deltas ───────────────
      // These are the messages that arrive token-by-token during model
      // generation. Buffering them avoids one full markdown render per
      // token. The reactive UI still updates promptly because a flush is
      // scheduled within STREAM_FLUSH_INTERVAL_MS of the first queued
      // delta. We flip the streaming flags immediately so the "typing"
      // cursor / status badge show up without waiting for the first flush.
      if (
        msg.type === 'thinking' ||
        msg.type === 'subagent_delta' ||
        ((msg.type === 'content' || msg.type === 'tool_call') &&
          '_delta' in msg &&
          !!msg._delta)
      ) {
        isStreaming.value = true
        streamActive.value = true
        streamDone.value = false

        if (msg.type === 'thinking') {
          _streamBuffer.push({ cid, kind: 'thinking', text: msg.content || '', model: msg.model })
        } else if (msg.type === 'content') {
          _streamBuffer.push({
            cid,
            kind: 'content',
            role: 'assistant',
            text: msg.content || '',
            model: msg.model,
          })
        } else if (msg.type === 'tool_call') {
          _streamBuffer.push({
            cid,
            kind: 'tool_call',
            tc: msg.content as ToolCallPayload,
            model: msg.model,
          })
        } else {
          const sdMsg = msg as {
            subagent_id: string
            delta_type: string
            content?: string | ToolCallPayload
            results?: ToolCallResultPayload[]
            model?: string
          }
          if (sdMsg.delta_type === 'thinking') {
            _streamBuffer.push({
              cid,
              kind: 'subagent_thinking',
              subagentId: sdMsg.subagent_id,
              text: (sdMsg.content as string) || '',
              model: sdMsg.model,
            })
          } else if (sdMsg.delta_type === 'content') {
            _streamBuffer.push({
              cid,
              kind: 'subagent_content',
              subagentId: sdMsg.subagent_id,
              text: (sdMsg.content as string) || '',
              model: sdMsg.model,
            })
          } else if (sdMsg.delta_type === 'tool_call') {
            const tcPayload = sdMsg.content as ToolCallPayload
            if (tcPayload && typeof tcPayload === 'object') {
              _streamBuffer.push({
                cid,
                kind: 'subagent_tool_call',
                subagentId: sdMsg.subagent_id,
                tc: tcPayload,
                model: sdMsg.model,
              })
            }
          } else if (sdMsg.delta_type === 'tool_call_results') {
            const results = sdMsg.results || []
            if (results.length) {
              _streamBuffer.push({
                cid,
                kind: 'subagent_tool_call_results',
                subagentId: sdMsg.subagent_id,
                results,
              })
            }
          }
        }
        _scheduleFlush()
        return
      }

      // ── Slow path: apply any queued deltas before this event ────────
      // Non-delta events (replays, full content, stream lifecycle, subagent
      // start/end/ref, tool-call results) need to see the up-to-date state
      // produced by previous deltas, so drain the buffer first.
      _flushStreamBuffer()

      if (!messagesByConv.value[cid]) messagesByConv.value[cid] = []
      const arr = messagesByConv.value[cid]

      switch (msg.type) {
        case 'stream_start':
          isTyping.value = false
          isStreaming.value = false
          streamActive.value = true
          streamDone.value = false
          _toolCallState = { current: null }
          _roundStart = arr.length
          break

        case 'stream_end':
          for (let i = _roundStart; i < arr.length; i++) {
            const converted = _tryConvertMessageToolCall(arr[i], cid)
            if (converted) arr[i] = converted
          }
          if (msg.time) {
            for (let i = arr.length - 1; i >= _roundStart; i--) {
              if (arr[i].type === 'content' && arr[i].role === 'assistant') {
                arr[i] = { ...arr[i], time: msg.time }
                break
              }
            }
          }
          isStreaming.value = false
          streamActive.value = false
          streamDone.value = true
          _toolCallState = { current: null }
          _roundStart = arr.length
          break

        // Session replay uses 'think' from _session_to_ui
        case 'think':
          arr.push(msg as ChatMessage)
          break

        case 'content': {
          if (msg.role === 'assistant') {
            isTyping.value = false
            isStreaming.value = false
          }
          const fp = msg.role + '\x00' + (msg.content || '')
          if (msg.role === 'user' && arr.slice(-5).some(m => m.role + '\x00' + (m.content || '') === fp)) {
            break
          }
          arr.push(msg as ChatMessage)
          _updatePreview(cid, msg.content)
          break
        }

        case 'tool_call': {
          const tc = msg.content
          const chatMsg = _toolCallPayloadToChatMessage(tc, cid, {
            tool_call_id: (msg as { tool_call_id?: string }).tool_call_id,
            tool_name: (msg as { tool_name?: string }).tool_name,
            model: (msg as { model?: string }).model,
          })
          if (chatMsg) {
            if (chatMsg.type === 'content') {
              const fp = chatMsg.role + '\x00' + chatMsg.content
              if (!arr.slice(-20).some(m => m.role === 'assistant' && (m.role + '\x00' + (m.content || '')) === fp))
                arr.push(chatMsg)
            } else {
              arr.push(chatMsg)
            }
          }
          break
        }

        case 'tool_call_results': {
          const results = (msg as { results?: ToolCallResultPayload[] }).results || []
          if (results.length) _applyToolCallResults(arr, cid, results)
          break
        }

        case 'subagent_start': {
          const sid = (msg as { subagent_id: string; label: string }).subagent_id
          const label = (msg as { subagent_id: string; label: string }).label
          if (!subagents.value[cid]) subagents.value[cid] = []
          const sessionKey = `subagent:${sid}`
          const existing = subagents.value[cid].find(s => s.id === sid || s.sessionId === sessionKey)
          if (!existing) {
            subagents.value[cid].push({ id: sid, label, status: 'running', items: [] })
            subagents.value = { ...subagents.value }
          }
          break
        }

        case 'subagent_end': {
          const seMsg = msg as { subagent_id: string; status: string; session_id?: string }
          const subs2 = subagents.value[cid] || []
          const sub2 = subs2.find(s => s.id === seMsg.subagent_id)
          if (sub2) {
            sub2.status = seMsg.status as 'ok' | 'error'
            if (seMsg.session_id) sub2.sessionId = seMsg.session_id
            subagents.value = { ...subagents.value }
          }
          break
        }

        case 'subagent_ref': {
          const refMsg = msg as { session_id: string; label: string; status: string; task?: string; announce?: string }
          if (!subagents.value[cid]) subagents.value[cid] = []
          const plainId = refMsg.session_id.replace(/^subagent:/, '')
          const existing = subagents.value[cid].find(s =>
            s.id === plainId || s.sessionId === refMsg.session_id
          )
          const finalStatus = _normalizeSubagentStatus(refMsg.status)
          const label = refMsg.label || refMsg.session_id
          _pushSubagentStatus(arr, cid, {
            id: plainId,
            sessionId: refMsg.session_id,
            label,
            event: 'finished',
            status: finalStatus,
            report: refMsg.announce,
            task: refMsg.task,
          })
          if (!existing) {
            subagents.value[cid].push({
              id: plainId,
              label,
              status: finalStatus,
              items: [],
              sessionId: refMsg.session_id,
            })
            subagents.value = { ...subagents.value }
            _fetchSubagentHistory(refMsg.session_id, cid)
          } else {
            let changed = false
            if (existing.status !== finalStatus) { existing.status = finalStatus; changed = true }
            if (!existing.sessionId) { existing.sessionId = refMsg.session_id; changed = true }
            if (refMsg.label && (!existing.label || existing.label === plainId)) {
              existing.label = refMsg.label; changed = true
            }
            if (changed) subagents.value = { ...subagents.value }
            if (!existing.items.length) _fetchSubagentHistory(refMsg.session_id, cid)
          }
          break
        }
      }
      scrollToBottom()
    }

    ws.onclose = () => {
      if (wsGeneration !== myGen) return
      wsStatus.value = 'disconnected'
      isTyping.value = false
      isStreaming.value = false
      streamActive.value = false
      streamDone.value = false
      wsReconnectTimer = window.setTimeout(() => {
        if (wsGeneration === myGen && activeId.value === convId) {
          messagesByConv.value[convId] = []
          for (const k of Object.keys(_openCache)) delete _openCache[k]
          _userOpenedKeys.clear()
          groupOpenState.value = {}
          connectWs(convId)
        }
      }, 3000)
    }

    ws.onerror = () => {
      if (wsGeneration !== myGen) return
      wsStatus.value = 'disconnected'
      isTyping.value = false
      isStreaming.value = false
      streamActive.value = false
      streamDone.value = false
    }
  }

  async function newConversation(): Promise<void> {
    const res = await fetch('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    const data = await res.json() as { conversation_id: string }
    const cid = data.conversation_id
    conversations.value.unshift({ id: cid, preview: '' })
    messagesByConv.value[cid] = []
    selectConversation(cid)
  }

  function selectConversation(id: string): void {
    activeId.value = id
    isTyping.value = false
    isStreaming.value = false
    streamActive.value = false
    _clearStreamBuffer()
    for (const k of Object.keys(_openCache)) delete _openCache[k]
    _userOpenedKeys.clear()
    groupOpenState.value = {}
    messagesByConv.value[id] = []
    subagents.value[id] = []
    _userScrolledUp = false
    connectWs(id)
    nextTick(() => {
      _observeMessagesInner()
      scrollToBottom(true)
    })
  }

  async function deleteConversation(id: string): Promise<void> {
    try {
      await fetch(`/api/conversations/${id}`, { method: 'DELETE' })
    } catch (_) {}
    conversations.value = conversations.value.filter(c => c.id !== id)
    delete messagesByConv.value[id]
    if (activeId.value === id) {
      activeId.value = null
      _closeWs()
    }
  }

  async function sendMessage(media: string[] = []): Promise<void> {
    const text = inputText.value.trim()
    if (!text && !media.length || !activeId.value || isTyping.value) return

    if (text.startsWith('/') && !media.length) {
      inputText.value = ''
      sendCommand(text.slice(1))
      return
    }

    inputText.value = ''
    isTyping.value = true
    // Flip the "generating" status on immediately so the just-sent user
    // bubble picks up its spinner right away. Without this there's a brief
    // window (until the backend's `stream_start` ack arrives) where no
    // status is attached to anything, which used to be filled by a
    // separate assistant-styled "Thinking…" placeholder — that placeholder
    // then vanished the instant `stream_start` landed, reading as a flash
    // of an assistant bubble that immediately morphs into the user-side
    // indicator. Attaching the spinner to the user message from the start
    // skips that flash entirely.
    streamActive.value = true
    streamDone.value = false

    const cid = activeId.value
    messagesByConv.value[cid].push({
      type: 'content', role: 'user', content: text, media, conversation_id: cid,
    })
    scrollToBottom(true)

    const c = conversations.value.find(c => c.id === cid)
    if (c) c.preview = text.slice(0, 80)

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'content', content: text, media }))
    } else {
      try {
        await fetch(`/api/conversations/${cid}/message`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: text, media }),
        })
      } catch {
        isTyping.value = false
      }
    }
  }

  async function sendCommand(cmd: string): Promise<void> {
    if (!activeId.value) return
    isTyping.value = true
    streamActive.value = true
    streamDone.value = false
    const cid = activeId.value
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'command', command: cmd }))
    } else {
      try {
        await fetch(`/api/conversations/${cid}/command`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: cmd }),
        })
      } catch {
        isTyping.value = false
      }
    }
  }

  async function stopGeneration(): Promise<void> {
    if (!activeId.value) return
    const cid = activeId.value
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'stop' }))
    } else {
      try {
        await fetch(`/api/conversations/${cid}/stop`, { method: 'POST' })
      } catch (_) {}
    }
  }

  function _isAtBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_TOLERANCE
  }

  function _pinToBottom(el: HTMLElement): void {
    // Override the container's CSS `scroll-behavior: smooth` with an
    // explicit instant scroll. Smooth scrolling can't keep up with the
    // moving target during rapid streaming deltas, leaving the viewport
    // visibly trailing behind the actual bottom.
    el.scrollTo({ top: el.scrollHeight, behavior: 'auto' })
  }

  // How many extra animation-frame passes to re-pin for after the initial
  // scroll. A single follow-up frame isn't always enough — e.g. an <img>
  // that decodes a frame or two after layout, or a code block whose
  // highlighting reflows just after paint — so we keep nudging the
  // scroll position to the (possibly still-growing) bottom for a few
  // frames instead of a single fire-and-forget correction.
  const SCROLL_SETTLE_FRAMES = 6

  function scrollToBottom(force = false): void {
    if (force) _userScrolledUp = false
    nextTick(() => {
      const el = messagesEl.value
      if (!el) return
      if (!force && _userScrolledUp) return
      _pinToBottom(el)

      let framesLeft = SCROLL_SETTLE_FRAMES
      const step = () => {
        const cur = messagesEl.value
        if (!cur) return
        if (!force && _userScrolledUp) return
        _pinToBottom(cur)
        framesLeft--
        if (framesLeft > 0) requestAnimationFrame(step)
      }
      requestAnimationFrame(step)
    })
  }

  function _onMessagesScroll(): void {
    const el = messagesEl.value
    if (!el) return
    _userScrolledUp = !_isAtBottom(el)
  }

  // <img> `load` events don't bubble, but a capturing listener on an
  // ancestor still observes them during the capture phase. Attachment
  // images/media can finish decoding well after the initial scroll pass
  // (especially over a slow connection), growing `.messages-inner` after
  // our rAF settle loop above has already given up — without this, the
  // viewport is left short of the true bottom until the next unrelated
  // message triggers another scroll.
  function _onMediaLoad(e: Event): void {
    if (!(e.target instanceof HTMLImageElement)) return
    scrollToBottom()
  }

  function _ensureResizeObserver(): void {
    if (_resizeObserver || typeof ResizeObserver === 'undefined') return
    _resizeObserver = new ResizeObserver(() => {
      const cur = messagesEl.value
      if (!cur) return
      if (_userScrolledUp) return
      _pinToBottom(cur)
    })
  }

  function _observeMessagesInner(): void {
    _ensureResizeObserver()
    const el = messagesEl.value
    if (!el || !_resizeObserver) return
    const inner = el.querySelector('.messages-inner') as HTMLElement | null
    if (inner === _observedInner) return
    if (_observedInner) _resizeObserver.unobserve(_observedInner)
    _observedInner = inner
    if (inner) _resizeObserver.observe(inner)
  }

  function autoResize(e: Event): void {
    const el = e.target as HTMLTextAreaElement
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }

  function openLightbox(src: string): void {
    lightboxSrc.value = src
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') lightboxSrc.value = null
  }

  onMounted(async () => {
    window.addEventListener('keydown', onKeydown)
    nextTick(() => {
      const el = messagesEl.value
      if (el) {
        _scrollHandler = _onMessagesScroll
        el.addEventListener('scroll', _scrollHandler, { passive: true })
        el.addEventListener('load', _onMediaLoad, { capture: true, passive: true })
      }
      _observeMessagesInner()
    })
    try {
      const res = await fetch('/api/conversations')
      const data = await res.json() as { conversations?: Array<{ id: string; last_message?: string }> }
      conversations.value = (data.conversations || []).map(c => ({
        id: c.id,
        preview: c.last_message || '',
      }))
    } catch (_) {}
  })

  onUnmounted(() => {
    window.removeEventListener('keydown', onKeydown)
    if (messagesEl.value && _scrollHandler) {
      messagesEl.value.removeEventListener('scroll', _scrollHandler)
      messagesEl.value.removeEventListener('load', _onMediaLoad, { capture: true })
    }
    _scrollHandler = null
    if (_resizeObserver) {
      _resizeObserver.disconnect()
      _resizeObserver = null
    }
    _observedInner = null
    _closeWs()
  })

  return {
    conversations,
    activeId,
    inputText,
    isTyping,
    isStreaming,
    streamActive,
    streamDone,
    wsStatus,
    wsStatusLabel,
    lightboxSrc,
    messagesEl,
    groupOpenState,
    setGroupOpen,
    messageGroups,
    currentSubagents,
    newConversation,
    selectConversation,
    deleteConversation,
    sendMessage,
    sendCommand,
    stopGeneration,
    openLightbox,
    autoResize,
    scrollToBottom,
  }
}
