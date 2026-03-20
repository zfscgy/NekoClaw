import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'

const WS_STATUS = { connected: 'Connected', connecting: 'Connecting…', disconnected: 'Disconnected' }

export function useChat() {
  const conversations = ref([])
  const activeId = ref(null)
  const messagesByConv = ref({})
  const inputText = ref('')
  const isTyping = ref(false)
  const streamingContent = ref(null)
  const streamingThinking = ref(null)
  // index → { name: string, arguments: string } — live tool call argument build-up
  const streamingToolCallDeltas = ref({})
  // Ordered array of streaming items for time-ordered rendering.
  // Each item: { kind: 'thinking'|'tool_call'|'content', content, [index, name, arguments] }
  const streamingItems = ref([])
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
      if (m.type === 'tool_call' || m.type === 'progress' || m.type === 'reasoning_response') {
        const items = []
        while (i < msgs.length && (msgs[i].type === 'tool_call' || msgs[i].type === 'progress' || msgs[i].type === 'reasoning_response')) {
          items.push(msgs[i])
          i++
        }
        const isLive = i >= msgs.length
        const key = 'ag-' + groups.length
        if (_openCache[key] === undefined) pendingOpen[key] = isLive
        else if (isLive && !_openCache[key]) pendingOpen[key] = true
        groups.push({ type: 'actions', items, key })
      } else {
        if (groups.length && groups[groups.length - 1].type === 'actions') {
          const prev = groups[groups.length - 1]
          if (_openCache[prev.key] !== false) pendingOpen[prev.key] = false
        }
        groups.push({ type: 'message', role: m.role, content: m.content, media: m.media || [] })
        i++
      }
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

  function _clearStreamingState() {
    streamingContent.value = null
    streamingThinking.value = null
    streamingToolCallDeltas.value = {}
    streamingItems.value = []
  }

  function _closeWs() {
    wsGeneration++
    clearTimeout(wsReconnectTimer)
    if (ws) {
      try { ws.close() } catch (_) {}
      ws = null
    }
    wsStatus.value = 'disconnected'
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

      if (msg.type === 'stream_start') {
        isTyping.value = false
        streamingContent.value = ''
        streamingThinking.value = null
        streamingToolCallDeltas.value = {}
        streamingItems.value = []
        scrollToBottom()
        return
      }

      if (msg.type === 'stream_delta') {
        if (streamingContent.value !== null) streamingContent.value += msg.content || ''
        const items = streamingItems.value
        const last = items.length ? items[items.length - 1] : null
        if (last && last.kind === 'content') {
          items[items.length - 1] = { kind: 'content', content: last.content + (msg.content || '') }
        } else {
          items.push({ kind: 'content', content: msg.content || '' })
        }
        scrollToBottom()
        return
      }

      if (msg.type === 'stream_think_delta') {
        if (streamingThinking.value === null) streamingThinking.value = ''
        streamingThinking.value += msg.content || ''
        const items = streamingItems.value
        const last = items.length ? items[items.length - 1] : null
        if (last && last.kind === 'thinking') {
          items[items.length - 1] = { kind: 'thinking', content: last.content + (msg.content || '') }
        } else {
          items.push({ kind: 'thinking', content: msg.content || '' })
        }
        scrollToBottom()
        return
      }

      if (msg.type === 'stream_tool_call_delta') {
        const delta = JSON.parse(msg.content)
        streamingToolCallDeltas.value = {
          ...streamingToolCallDeltas.value,
          [delta.index]: { name: delta.name, arguments: delta.arguments },
        }
        const items = streamingItems.value
        const existingIdx = items.findIndex(i => i.kind === 'tool_call' && i.index === delta.index)
        if (existingIdx >= 0) {
          items[existingIdx] = {
            kind: 'tool_call', index: delta.index,
            name: delta.name || items[existingIdx].name,
            arguments: delta.arguments,
          }
        } else {
          items.push({ kind: 'tool_call', index: delta.index, name: delta.name || '', arguments: delta.arguments || '' })
        }
        scrollToBottom()
        return
      }

      if (msg.type === 'stream_end') {
        if (streamingThinking.value) {
          const arr = messagesByConv.value[cid]
          arr.push({ type: 'progress', content: streamingThinking.value, conversation_id: cid })
        }
        streamingContent.value = null
        streamingThinking.value = null
        streamingToolCallDeltas.value = {}
        streamingItems.value = []
        return
      }

      if (msg.type === 'raw_response') {
        // Final LLM text response (agent did not use the message tool).
        // Always display as a message bubble regardless of whether thinking
        // was active — the thinking was already persisted at stream_end.
        isTyping.value = false
        _clearStreamingState()

        const arr = messagesByConv.value[cid]
        const fp = '\x00' + (msg.content || '')
        const isDup = arr.slice(-20).some(m =>
          (m.type === 'raw_response' || m.type === 'reasoning_response' || m.role === 'assistant') &&
          ('\x00' + (m.content || '')) === fp
        )
        if (!isDup) {
          arr.push({ ...msg, type: 'message', role: 'assistant' })
          const c = conversations.value.find(c => c.id === cid)
          if (c) c.preview = (msg.content || '').slice(0, 80)
        }
        scrollToBottom()
        return
      }

      if (msg.type === 'message' && msg.role === 'assistant') {
        isTyping.value = false
        _clearStreamingState()
      }

      if (msg.type === 'message') {
        const arr = messagesByConv.value[cid]
        const fp = msg.role + '\x00' + (msg.content || '')
        const isDup = arr.slice(-20).some(m => m.role === msg.role && (m.role + '\x00' + (m.content || '')) === fp)
        if (!isDup) {
          arr.push(msg)
          const c = conversations.value.find(c => c.id === cid)
          if (c) c.preview = (msg.content || '').slice(0, 80)
        }
      } else if (msg.type === 'progress') {
        const arr = messagesByConv.value[cid]
        if (arr.length && arr[arr.length - 1].type === 'progress') {
          arr[arr.length - 1] = msg
        } else {
          arr.push(msg)
        }
      } else {
        // For tool_call messages, flush any accumulated thinking first to preserve time order.
        if (msg.type === 'tool_call' && streamingThinking.value) {
          messagesByConv.value[cid].push({ type: 'progress', content: streamingThinking.value, conversation_id: cid })
          streamingThinking.value = null
        }
        messagesByConv.value[cid].push(msg)
      }
      scrollToBottom()
    }

    ws.onclose = () => {
      if (wsGeneration !== myGen) return
      wsStatus.value = 'disconnected'
      isTyping.value = false
      _clearStreamingState()
      wsReconnectTimer = setTimeout(() => {
        if (wsGeneration === myGen && activeId.value === convId) connectWs(convId)
      }, 3000)
    }

    ws.onerror = () => {
      if (wsGeneration !== myGen) return
      wsStatus.value = 'disconnected'
      isTyping.value = false
      _clearStreamingState()
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
    _clearStreamingState()
    for (const k of Object.keys(_openCache)) delete _openCache[k]
    groupOpenState.value = {}
    if (!messagesByConv.value[id]) messagesByConv.value[id] = []
    connectWs(id)
    nextTick(scrollToBottom)
  }

  function deleteConversation(id) {
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
      type: 'message', role: 'user', content: text, media: [], conversation_id: cid,
    })
    scrollToBottom()

    const c = conversations.value.find(c => c.id === cid)
    if (c) c.preview = text.slice(0, 80)

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'message', content: text, media: [] }))
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

  onMounted(() => window.addEventListener('keydown', onKeydown))
  onUnmounted(() => {
    window.removeEventListener('keydown', onKeydown)
    _closeWs()
  })

  return {
    conversations,
    activeId,
    inputText,
    isTyping,
    streamingContent,
    streamingThinking,
    streamingToolCallDeltas,
    streamingItems,
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
