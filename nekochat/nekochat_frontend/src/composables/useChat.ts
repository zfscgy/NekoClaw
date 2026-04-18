import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'

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
  type: 'content' | 'think' | 'tool_call' | 'reasoning_response'
  role?: string
  content?: string
  media?: string[]
  conversation_id?: string
  _replay?: boolean
  time?: string
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
}

export interface ActionsGroup {
  type: 'actions'
  items: ChatMessage[]
  key: string
  appendCursor?: boolean
  streamStatus?: StreamStatus
}

export type MessageGroup = ContentGroup | ActionsGroup

// Tool call payload from the backend (matches ToolCallRequest fields)
interface ToolCallPayload {
  index: number
  id: string
  name: string
  arguments: Record<string, unknown>
  partial: boolean
}

// Discriminated union for incoming WebSocket messages.
// Streaming deltas carry `_delta: true`; session-replay entries carry `_replay: true`.
type WsMessage =
  | { type: 'stream_start'; conversation_id?: string }
  | { type: 'stream_end'; conversation_id?: string; time?: string }
  | { type: 'thinking'; content?: string; conversation_id?: string; _delta?: boolean }
  | { type: 'content'; role?: string; content?: string; media?: string[]; conversation_id?: string; _replay?: boolean; _delta?: boolean; time?: string }
  | { type: 'think'; role?: string; content?: string; media?: string[]; conversation_id?: string; _replay?: boolean }
  | { type: 'tool_call'; role?: string; content?: string | ToolCallPayload; media?: string[]; conversation_id?: string; _replay?: boolean; _delta?: boolean }
  | { type: 'subagent_start'; subagent_id: string; label: string; conversation_id?: string; _replay?: boolean }
  | { type: 'subagent_delta'; subagent_id: string; delta_type: 'thinking' | 'content' | 'tool_call'; content?: string | ToolCallPayload; conversation_id?: string }
  | { type: 'subagent_end'; subagent_id: string; status: string; conversation_id?: string }
  | { type: 'subagent_ref'; session_id: string; label: string; status: string; task?: string; conversation_id?: string; _replay?: boolean }

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

function _toolCallPayloadToChatMessage(
  tc: string | ToolCallPayload | undefined,
  cid: string,
): ChatMessage | null {
  if (!tc) return null

  // Legacy: string content from session replay
  if (typeof tc === 'string') {
    if (tc.startsWith('send_message_with_attachments(')) {
      const argsStr = tc.slice('send_message_with_attachments('.length, tc.endsWith(')') ? -1 : undefined)
      const args = _safeJsonParse(argsStr)
      if (args && typeof args === 'object')
        return { type: 'content', role: 'assistant', content: args.content || '', media: Array.isArray(args.media) ? args.media : [], conversation_id: cid }
    }
    return { type: 'tool_call', content: tc, conversation_id: cid }
  }

  // Structured ToolCallPayload from backend
  if (tc.name === 'send_message_with_attachments' && !tc.partial) {
    const args = tc.arguments as Record<string, unknown>
    if (args && typeof args === 'object')
      return { type: 'content', role: 'assistant', content: (args.content as string) || '', media: Array.isArray(args.media) ? args.media as string[] : [], conversation_id: cid }
  }

  return { type: 'tool_call', content: _formatToolCall({ name: tc.name || '', arguments: _stringifyToolArguments(tc.arguments) }), conversation_id: cid }
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
  }
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

  function setGroupOpen(key: string, val: boolean): void {
    _openCache[key] = val
    groupOpenState.value = { ...groupOpenState.value, [key]: val }
  }

  const messageGroups = computed<MessageGroup[]>(() => {
    const msgs = currentMessages.value
    const groups: MessageGroup[] = []
    const pendingOpen: Record<string, boolean> = {}
    let i = 0

    while (i < msgs.length) {
      const m = msgs[i]

      if (m.type === 'tool_call' || m.type === 'think' || m.type === 'reasoning_response') {
        const items: ChatMessage[] = []
        while (i < msgs.length && (msgs[i].type === 'tool_call' || msgs[i].type === 'think' || msgs[i].type === 'reasoning_response')) {
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
          if (prev.type === 'actions' && _openCache[prev.key] !== false) pendingOpen[prev.key] = false
        }
        groups.push({ type: 'content', role: m.role, content: m.content, media: m.media || [], time: m.time })
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

  function _appendStreamText(arr: ChatMessage[], type: ChatMessage['type'], role: string | undefined, text: string): void {
    if (!text) return
    const last = arr.length ? arr[arr.length - 1] : null
    if (last && last.type === type && (!role || last.role === role)) {
      arr[arr.length - 1] = { ...last, content: (last.content || '') + text }
    } else {
      const entry: ChatMessage = { type, content: text }
      if (role) entry.role = role
      arr.push(entry)
    }
  }

  function _handleToolCallDelta(arr: ChatMessage[], cid: string, tc: ToolCallPayload): void {
    let current = _toolCallState.current
    const incomingId = tc.id || null

    const needNewSlot =
      !current ||
      (incomingId != null && current.toolCallId != null && incomingId !== current.toolCallId)

    if (needNewSlot) {
      current = { name: '', arguments: '', arrIdx: arr.length, toolCallId: incomingId }
      _toolCallState.current = current
      arr.push({ type: 'tool_call', content: _formatToolCall(current), conversation_id: cid })
    }

    if (tc.name) current!.name = tc.name
    const hasArgs = tc.arguments && Object.keys(tc.arguments).length > 0
    if (hasArgs) current!.arguments = _stringifyToolArguments(tc.arguments)
    if (incomingId != null) current!.toolCallId = incomingId
    arr[current!.arrIdx] = { ...arr[current!.arrIdx], content: _formatToolCall(current!) }

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
          }
        }
      }
      _toolCallState.current = null
    }
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
        sub.items = data.history
        sub.sessionId = sessionId
        subagents.value = { ...subagents.value }
      }
    } catch {
      // ignore fetch errors
    }
  }

  // ── WebSocket ──────────────────────────────────────────────────────

  function _closeWs(): void {
    wsGeneration++
    clearTimeout(wsReconnectTimer)
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

        case 'thinking':
          isStreaming.value = true
          streamActive.value = true
          _appendStreamText(arr, 'think', undefined, msg.content || '')
          break

        // Session replay uses 'think' from _session_to_ui
        case 'think':
          arr.push(msg as ChatMessage)
          break

        case 'content': {
          if ('_delta' in msg && msg._delta) {
            isStreaming.value = true
            streamActive.value = true
            _appendStreamText(arr, 'content', 'assistant', msg.content || '')
            break
          }
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
          if ('_delta' in msg && msg._delta) {
            isStreaming.value = true
            streamActive.value = true
            _handleToolCallDelta(arr, cid, msg.content as ToolCallPayload)
            break
          }
          const tc = msg.content
          const chatMsg = _toolCallPayloadToChatMessage(tc, cid)
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

        case 'subagent_delta': {
          const sdMsg = msg as { subagent_id: string; delta_type: string; content?: string | ToolCallPayload }
          const subs = subagents.value[cid] || []
          const sub = subs.find(s => s.id === sdMsg.subagent_id)
          if (sub) {
            if (sdMsg.delta_type === 'thinking') {
              _appendStreamText(sub.items, 'think', undefined, (sdMsg.content as string) || '')
            } else if (sdMsg.delta_type === 'content') {
              _appendStreamText(sub.items, 'content', 'assistant', (sdMsg.content as string) || '')
            } else if (sdMsg.delta_type === 'tool_call') {
              const tcPayload = sdMsg.content as ToolCallPayload
              if (tcPayload && typeof tcPayload === 'object') {
                const formatted = _formatToolCall({ name: tcPayload.name || '', arguments: _stringifyToolArguments(tcPayload.arguments) })
                if (!tcPayload.partial) {
                  sub.items.push({ type: 'tool_call', content: formatted, conversation_id: cid })
                } else {
                  _appendStreamText(sub.items, 'tool_call', undefined, '')
                  const last = sub.items[sub.items.length - 1]
                  if (last) last.content = formatted
                }
              }
            }
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
          const refMsg = msg as { session_id: string; label: string; status: string; task?: string }
          if (!subagents.value[cid]) subagents.value[cid] = []
          const plainId = refMsg.session_id.replace(/^subagent:/, '')
          const existing = subagents.value[cid].find(s =>
            s.id === plainId || s.sessionId === refMsg.session_id
          )
          if (!existing) {
            const sub: SubagentState = {
              id: plainId,
              label: refMsg.label || refMsg.session_id,
              status: (refMsg.status as 'ok' | 'error') || 'ok',
              items: [],
              sessionId: refMsg.session_id,
            }
            subagents.value[cid].push(sub)
            subagents.value = { ...subagents.value }
            _fetchSubagentHistory(refMsg.session_id, cid)
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
    for (const k of Object.keys(_openCache)) delete _openCache[k]
    groupOpenState.value = {}
    messagesByConv.value[id] = []
    subagents.value[id] = []
    connectWs(id)
    nextTick(scrollToBottom)
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
    streamActive.value = false
    streamDone.value = false

    const cid = activeId.value
    messagesByConv.value[cid].push({
      type: 'content', role: 'user', content: text, media, conversation_id: cid,
    })
    scrollToBottom()

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
    streamActive.value = false
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

  function scrollToBottom(): void {
    nextTick(() => {
      if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    })
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
    _closeWs()
  })

  return {
    conversations,
    activeId,
    inputText,
    isTyping,
    isStreaming,
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
    openLightbox,
    autoResize,
    scrollToBottom,
  }
}
