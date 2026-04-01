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

interface NormalizedToolCall {
  index: number
  name: string
  arguments: string
  toolCallId: string
}

interface ParsedToolCallDelta {
  index: number
  name: string
  arguments: string
}

export type StreamStatus = 'generating' | 'complete'

export interface ContentGroup {
  type: 'content'
  role?: string
  content?: string
  media: string[]
  appendCursor?: boolean
  streamStatus?: StreamStatus
}

export interface ActionsGroup {
  type: 'actions'
  items: ChatMessage[]
  key: string
  appendCursor?: boolean
  streamStatus?: StreamStatus
}

export type MessageGroup = ContentGroup | ActionsGroup

// Discriminated union for incoming WebSocket messages
type WsMessage =
  | { type: 'stream_start'; conversation_id?: string }
  | { type: 'stream_end'; conversation_id?: string }
  | { type: 'stream_content_delta'; role?: string; content?: string; conversation_id?: string }
  | { type: 'stream_think_delta'; content?: string; conversation_id?: string }
  | { type: 'stream_tool_call_delta'; content: string; conversation_id?: string }
  | { type: 'content'; role?: string; content?: string; media?: string[]; conversation_id?: string; _replay?: boolean }
  | { type: 'think'; role?: string; content?: string; media?: string[]; conversation_id?: string; _replay?: boolean }
  | { type: 'tool_call'; role?: string; content?: string; media?: string[]; conversation_id?: string; _replay?: boolean }
  | { type: string; conversation_id?: string }

// ── Private helpers ────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function _safeJsonParse(value: string): any {
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

function _extractPartialStringField(raw: string, key: string): string | null {
  if (!raw || typeof raw !== 'string') return null
  const marker = `"${key}":`
  const start = raw.indexOf(marker)
  if (start < 0) return null

  let i = start + marker.length
  while (i < raw.length && /\s/.test(raw[i])) i++
  if (raw[i] !== '"') return null
  i += 1

  let out = ''
  let escaped = false
  for (; i < raw.length; i += 1) {
    const ch = raw[i]
    if (escaped) {
      if (ch === 'n') out += '\n'
      else if (ch === 'r') out += '\r'
      else if (ch === 't') out += '\t'
      else out += ch
      escaped = false
      continue
    }
    if (ch === '\\') {
      escaped = true
      continue
    }
    if (ch === '"') return out
    out += ch
  }
  return out
}

function _extractPartialNumberField(raw: string, key: string): number | null {
  if (!raw || typeof raw !== 'string') return null
  const match = raw.match(new RegExp(`"${key}"\\s*:\\s*(\\d+)`))
  return match ? Number(match[1]) : null
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

function _extractToolCallId(rawContent: string): string | null {
  return _extractPartialStringField(rawContent, 'id') ?? null
}

function _normalizeToolCallPayload(
  payload: unknown,
  previous: Partial<NormalizedToolCall> | null,
): NormalizedToolCall | null {
  if (!payload || typeof payload !== 'object') return null

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const obj = payload as any
  const candidate = Array.isArray(obj)
    ? (obj as unknown[]).find(item => item && typeof item === 'object')
    : obj

  if (!candidate || typeof candidate !== 'object') return null

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const c = candidate as Record<string, any>
  const fn = c.function && typeof c.function === 'object' ? c.function : null
  const rawName = c.name ?? fn?.name ?? previous?.name ?? ''
  const rawArguments = c.arguments ?? fn?.arguments ?? previous?.arguments ?? ''
  const rawIndex = c.index ?? previous?.index ?? 0
  const rawToolCallId = c.tool_call_id ?? c.id ?? previous?.toolCallId ?? ''

  return {
    index: Number.isFinite(Number(rawIndex)) ? Number(rawIndex) : (previous?.index ?? 0),
    name: typeof rawName === 'string' ? rawName : String(rawName || ''),
    arguments: _stringifyToolArguments(rawArguments),
    toolCallId: typeof rawToolCallId === 'string' ? rawToolCallId : String(rawToolCallId || ''),
  }
}

function _parseStreamingToolCallDelta(
  rawContent: string,
  previous: Partial<NormalizedToolCall> | null,
): ParsedToolCallDelta {
  const parsed = _safeJsonParse(rawContent)
  const normalized = _normalizeToolCallPayload(parsed, previous)
  if (normalized) return normalized

  const fallbackName = _extractPartialStringField(rawContent, 'name') ?? previous?.name ?? ''
  const fallbackArguments = _extractPartialStringField(rawContent, 'arguments')
  const fallbackIndex = _extractPartialNumberField(rawContent, 'index') ?? previous?.index ?? 0

  return {
    index: fallbackIndex,
    name: fallbackName,
    arguments: fallbackArguments ?? previous?.arguments ?? rawContent ?? '',
  }
}

function _formatToolCall(item: Pick<ToolCallSlot, 'name' | 'arguments'>): string {
  return `${item.name || 'tool'}(${item.arguments || ''})`
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
  const streamDone = ref(false)
  const wsStatus = ref<WsStatusKey>('disconnected')
  const lightboxSrc = ref<string | null>(null)
  const messagesEl = ref<HTMLElement | null>(null)
  const groupOpenState = ref<Record<string, boolean>>({})

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
        if (groups.length && groups[groups.length - 1].type === 'actions') {
          const prev = groups[groups.length - 1]
          if (_openCache[prev.key] !== false) pendingOpen[prev.key] = false
        }
        groups.push({ type: 'content', role: m.role, content: m.content, media: m.media || [] })
        i++
      }
    }

    if (isStreaming.value && groups.length) {
      groups[groups.length - 1] = { ...groups[groups.length - 1], appendCursor: true }
    }

    const streamStatus: StreamStatus | undefined = isStreaming.value
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

  function _handleToolCallDelta(arr: ChatMessage[], cid: string, rawContent: string): void {
    console.log("_handleToolCallDelta", rawContent)
    let current = _toolCallState.current
    const parsed = _safeJsonParse(rawContent)
    const incomingId: string | null = parsed
      ? (parsed.tool_call_id ?? parsed.id ?? null)
      : _extractToolCallId(rawContent)

    const needNewSlot =
      !current ||
      (incomingId != null && current.toolCallId != null && incomingId !== current.toolCallId)

    if (needNewSlot) {
      current = { name: '', arguments: '', arrIdx: arr.length, toolCallId: incomingId }
      _toolCallState.current = current
      arr.push({ type: 'tool_call', content: _formatToolCall(current), conversation_id: cid })
    }

    if (parsed) {
      if (parsed.name == 'send_message_with_attachments' && parsed.arguments != "") {
        const args = _safeJsonParse(parsed.arguments)
        if (!args || typeof args !== 'object') return
        const message: ChatMessage = {
          type: 'content',
          role: 'assistant',
          content: args.content || '',
          media: Array.isArray(args.media) ? args.media : [],
          conversation_id: cid,
        }
        arr.push(message)
        return
      }
    }

    const delta = _parseStreamingToolCallDelta(rawContent, {
      name: current!.name,
      arguments: current!.arguments,
      index: 0,
    })

    current!.name = delta.name
    current!.arguments = delta.arguments
    if (incomingId != null) current!.toolCallId = incomingId
    arr[current!.arrIdx] = { ...arr[current!.arrIdx], content: _formatToolCall(current!) }
  }

  function _updatePreview(cid: string, content: string | undefined): void {
    const c = conversations.value.find(c => c.id === cid)
    if (c) c.preview = (content || '').slice(0, 80)
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
          streamDone.value = false
          _toolCallState = { current: null }
          _roundStart = arr.length
          break

        case 'stream_content_delta':
          isStreaming.value = true
          _appendStreamText(arr, 'content', 'assistant', msg.content || '')
          break

        case 'stream_think_delta':
          isStreaming.value = true
          _appendStreamText(arr, 'think', undefined, msg.content || '')
          break

        case 'stream_tool_call_delta':
          isStreaming.value = true
          _handleToolCallDelta(arr, cid, msg.content)
          break

        case 'stream_end':
          for (let i = _roundStart; i < arr.length; i++) {
            const converted = _tryConvertMessageToolCall(arr[i], cid)
            if (converted) arr[i] = converted
          }
          isStreaming.value = false
          streamDone.value = true
          _toolCallState = { current: null }
          _roundStart = arr.length
          break

        case 'content': {
          if (msg.role === 'assistant') {
            isTyping.value = false
            isStreaming.value = false
          }
          const fp = msg.role + '\x00' + (msg.content || '')
          const isDup = arr.slice(-20).some(m => m.role === msg.role && (m.role + '\x00' + (m.content || '')) === fp)
          if (!isDup) {
            arr.push(msg as ChatMessage)
            _updatePreview(cid, msg.content)
          }
          break
        }

        case 'think':
          arr.push(msg as ChatMessage)
          break

        case 'tool_call': {
          const converted = _tryConvertMessageToolCall(msg as ChatMessage, cid)
          if (converted) {
            const fp = converted.role + '\x00' + converted.content
            if (!arr.slice(-20).some(m => m.role === 'assistant' && (m.role + '\x00' + (m.content || '')) === fp))
              arr.push(converted)
          } else if (!(msg.content && (msg as ChatMessage).content?.startsWith('send_message_with_attachments('))) {
            if (!arr.slice(-20).some(m => m.type === 'tool_call' && (m.content || '') === ((msg as ChatMessage).content || '')))
              arr.push(msg as ChatMessage)
          }
          break
        }

        // message_media, unknown types: silently ignored
      }
      scrollToBottom()
    }

    ws.onclose = () => {
      if (wsGeneration !== myGen) return
      wsStatus.value = 'disconnected'
      isTyping.value = false
      isStreaming.value = false
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
    for (const k of Object.keys(_openCache)) delete _openCache[k]
    groupOpenState.value = {}
    messagesByConv.value[id] = []
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

  async function sendMessage(): Promise<void> {
    const text = inputText.value.trim()
    if (!text || !activeId.value || isTyping.value) return

    if (text.startsWith('/')) {
      inputText.value = ''
      sendCommand(text.slice(1))
      return
    }

    inputText.value = ''
    isTyping.value = true
    streamDone.value = false

    const cid = activeId.value
    messagesByConv.value[cid].push({
      type: 'content', role: 'user', content: text, media: [], conversation_id: cid,
    })
    scrollToBottom()

    const c = conversations.value.find(c => c.id === cid)
    if (c) c.preview = text.slice(0, 80)

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'content', content: text, media: [] }))
    } else {
      try {
        await fetch(`/api/conversations/${cid}/message`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: text, media: [] }),
        })
      } catch {
        isTyping.value = false
      }
    }
  }

  async function sendCommand(cmd: string): Promise<void> {
    if (!activeId.value) return
    isTyping.value = true
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
