import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'

const WS_STATUS = { connected: 'Connected', connecting: 'Connecting…', disconnected: 'Disconnected' }

function _safeJsonParse(value) {
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

function _extractPartialStringField(raw, key) {
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

function _extractPartialNumberField(raw, key) {
  if (!raw || typeof raw !== 'string') return null
  const match = raw.match(new RegExp(`"${key}"\\s*:\\s*(\\d+)`))
  return match ? Number(match[1]) : null
}

function _stringifyToolArguments(value) {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function _extractToolCallId(rawContent) {
  return _extractPartialStringField(rawContent, 'id')
    ?? null
}

function _normalizeToolCallPayload(payload, previous) {
  if (!payload || typeof payload !== 'object') return null

  const candidate = Array.isArray(payload)
    ? payload.find(item => item && typeof item === 'object')
    : payload

  if (!candidate || typeof candidate !== 'object') return null

  const fn = candidate.function && typeof candidate.function === 'object' ? candidate.function : null
  const rawName = candidate.name ?? fn?.name ?? previous?.name ?? ''
  const rawArguments = candidate.arguments ?? fn?.arguments ?? previous?.arguments ?? ''
  const rawIndex = candidate.index ?? previous?.index ?? 0
  const rawToolCallId = candidate.tool_call_id ?? candidate.id ?? previous?.toolCallId ?? ''

  return {
    index: Number.isFinite(Number(rawIndex)) ? Number(rawIndex) : (previous?.index ?? 0),
    name: typeof rawName === 'string' ? rawName : String(rawName || ''),
    arguments: _stringifyToolArguments(rawArguments),
    toolCallId: typeof rawToolCallId === 'string' ? rawToolCallId : String(rawToolCallId || ''),
  }
}

function _parseStreamingToolCallDelta(rawContent, previous) {
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

function _formatToolCall(item) {
  return `${item.name || 'tool'}(${item.arguments || ''})`
}

function _tryConvertMessageToolCall(entry, cid) {
  if (entry.type !== 'tool_call') return null
  const raw = entry.content || ''
  if (!raw.startsWith('message(')) return null
  const argsStr = raw.slice('message('.length, raw.endsWith(')') ? -1 : undefined)
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

export function useChat() {
  const conversations = ref([])
  const activeId = ref(null)
  const messagesByConv = ref({})
  const inputText = ref('')
  const isTyping = ref(false)
  const isStreaming = ref(false)
  const wsStatus = ref('disconnected')
  const lightboxSrc = ref(null)
  const messagesEl = ref(null)
  const groupOpenState = ref({})

  const wsStatusLabel = computed(() => WS_STATUS[wsStatus.value])

  const currentMessages = computed(() =>
    activeId.value ? (messagesByConv.value[activeId.value] || []) : []
  )

  let ws = null
  let wsReconnectTimer = null
  let wsGeneration = 0
  const _openCache = {}

  // Per-round streaming state (reset on stream_start / stream_end)
  let _toolCallState = { current: null }
  let _roundStart = 0

  function setGroupOpen(key, val) {
    _openCache[key] = val
    groupOpenState.value = { ...groupOpenState.value, [key]: val }
  }

  const messageGroups = computed(() => {
    const msgs = currentMessages.value
    const groups = []
    const pendingOpen = {}
    let i = 0

    while (i < msgs.length) {
      const m = msgs[i]

      if (m.type === 'tool_call' || m.type === 'think' || m.type === 'reasoning_response') {
        const items = []
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

  function _appendStreamText(arr, type, role, text) {
    if (!text) return
    const last = arr.length ? arr[arr.length - 1] : null
    if (last && last.type === type && (!role || last.role === role)) {
      arr[arr.length - 1] = { ...last, content: (last.content || '') + text }
    } else {
      const entry = { type, content: text }
      if (role) entry.role = role
      arr.push(entry)
    }
  }

  function _handleToolCallDelta(arr, cid, rawContent) {
    let current = _toolCallState.current
    if (!current || current.complete) {
      current = { name: '', arguments: '', arrIdx: arr.length, complete: false }
      _toolCallState.current = current
      arr.push({ type: 'tool_call', content: _formatToolCall(current), conversation_id: cid })
    }

    const delta = _parseStreamingToolCallDelta(rawContent, {
      name: current.name,
      arguments: current.arguments,
      index: 0,
    })

    current.name = delta.name
    current.arguments = delta.arguments
    arr[current.arrIdx] = { ...arr[current.arrIdx], content: _formatToolCall(current) }

    // In-order stream guarantee: once this JSON chunk is complete, next chunk is a new tool call.
    if (_safeJsonParse(rawContent)) current.complete = true
  }

  function _updatePreview(cid, content) {
    const c = conversations.value.find(c => c.id === cid)
    if (c) c.preview = (content || '').slice(0, 80)
  }

  // ── WebSocket ──────────────────────────────────────────────────────

  function _closeWs() {
    wsGeneration++
    clearTimeout(wsReconnectTimer)
    if (ws) {
      try { ws.close() } catch (_) {}
      ws = null
    }
    wsStatus.value = 'disconnected'
    isStreaming.value = false
  }

  function connectWs(convId) {
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

    ws.onmessage = (ev) => {
      if (wsGeneration !== myGen) return
      const msg = JSON.parse(ev.data)
      const cid = msg.conversation_id || convId
      if (!messagesByConv.value[cid]) messagesByConv.value[cid] = []
      const arr = messagesByConv.value[cid]

      switch (msg.type) {
        case 'stream_start':
          isTyping.value = false
          isStreaming.value = false
          _toolCallState = { current: null }
          _roundStart = arr.length
          break

        case 'stream_content_delta':
          isStreaming.value = true
          _appendStreamText(arr, 'content', 'assistant', msg.content || '')
          break

        case 'stream_think_delta':
          isStreaming.value = true
          _appendStreamText(arr, 'think', null, msg.content || '')
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
            arr.push(msg)
            _updatePreview(cid, msg.content)
          }
          break
        }

        case 'think':
          arr.push(msg)
          break

        case 'tool_call': {
          const converted = _tryConvertMessageToolCall(msg, cid)
          if (converted) {
            const fp = converted.role + '\x00' + converted.content
            if (!arr.slice(-20).some(m => m.role === 'assistant' && (m.role + '\x00' + (m.content || '')) === fp))
              arr.push(converted)
          } else {
            if (!arr.slice(-20).some(m => m.type === 'tool_call' && (m.content || '') === (msg.content || '')))
              arr.push(msg)
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
      wsReconnectTimer = setTimeout(() => {
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
    }
  }

  async function newConversation() {
    const res = await fetch('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    const data = await res.json()
    const cid = data.conversation_id
    conversations.value.unshift({ id: cid, preview: '' })
    messagesByConv.value[cid] = []
    selectConversation(cid)
  }

  function selectConversation(id) {
    activeId.value = id
    isTyping.value = false
    isStreaming.value = false
    for (const k of Object.keys(_openCache)) delete _openCache[k]
    groupOpenState.value = {}
    messagesByConv.value[id] = []
    connectWs(id)
    nextTick(scrollToBottom)
  }

  async function deleteConversation(id) {
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

  async function sendMessage() {
    const text = inputText.value.trim()
    if (!text || !activeId.value || isTyping.value) return

    if (text.startsWith('/')) {
      inputText.value = ''
      sendCommand(text.slice(1))
      return
    }

    inputText.value = ''
    isTyping.value = true

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

  async function sendCommand(cmd) {
    if (!activeId.value) return
    isTyping.value = true
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

  function scrollToBottom() {
    nextTick(() => {
      if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    })
  }

  function autoResize(e) {
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }

  function openLightbox(src) {
    lightboxSrc.value = src
  }

  function onKeydown(e) {
    if (e.key === 'Escape') lightboxSrc.value = null
  }

  onMounted(async () => {
    window.addEventListener('keydown', onKeydown)
    try {
      const res = await fetch('/api/conversations')
      const data = await res.json()
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
