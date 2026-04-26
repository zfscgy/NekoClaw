<template>
  <div class="subagent-card" :class="agent.status">
    <div class="subagent-header" @click="isOpen = !isOpen">
      <span class="subagent-status-dot" :class="agent.status"></span>
      <span class="subagent-label">{{ agent.label }}</span>
      <span class="subagent-badge">{{ statusText }}</span>
      <span class="subagent-chevron" :class="{ open: isOpen }">▶</span>
    </div>
    <div v-if="isOpen" ref="bodyEl" class="subagent-body">
      <div
        v-for="(item, i) in agent.items"
        :key="i"
        class="subagent-item"
        :class="itemClass(item)"
      >
        <div v-if="item.type === 'think'" class="subagent-think">{{ item.content }}</div>
        <div v-else-if="item.type === 'tool_call'" class="subagent-tool">
          <span class="tool-name">{{ toolDisplay(item.content).name }}</span>{{ toolDisplay(item.content).rest }}
        </div>
        <div v-else class="subagent-content" v-html="renderMarkdown(item.content || '')"></div>
      </div>
      <div v-if="agent.status === 'running' && !agent.items.length" class="subagent-thinking">
        <div class="typing-dots"><span></span><span></span><span></span></div>
        Working…
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { renderMarkdown } from '../utils/markdown'
import type { SubagentState, ChatMessage } from '../composables/useChat'

const STATUS_LABELS: Record<string, string> = {
  running: 'Running',
  ok: 'Done',
  error: 'Failed',
}

const props = defineProps<{
  agent: SubagentState
}>()

const isOpen = ref(props.agent.status === 'running')
const bodyEl = ref<HTMLElement | null>(null)

function scrollToBottom() {
  nextTick(() => {
    if (bodyEl.value) bodyEl.value.scrollTop = bodyEl.value.scrollHeight
  })
}

watch(() => props.agent.items.length, scrollToBottom, { flush: 'post' })
watch(() => props.agent.items[props.agent.items.length - 1]?.content, scrollToBottom, { flush: 'post' })

watch(() => props.agent.status, (s) => {
  if (s !== 'running') {
    setTimeout(() => { isOpen.value = false }, 2000)
  }
})

const statusText = computed(() => STATUS_LABELS[props.agent.status] || props.agent.status)

function itemClass(item: ChatMessage): string {
  if (item.type === 'think') return 'is-think'
  if (item.type === 'tool_call') return 'is-tool'
  return 'is-content'
}

/** Persisted subagent history uses JSON tool payloads; live items use formatted strings. */
function toolDisplay(content: unknown): { name: string; rest: string } {
  if (content != null && typeof content === 'object' && !Array.isArray(content)) {
    const o = content as { name?: string; arguments?: unknown }
    const n = (o.name && String(o.name)) || 'tool'
    let argsStr = ''
    try {
      if (typeof o.arguments === 'string') argsStr = o.arguments
      else argsStr = JSON.stringify(o.arguments ?? {})
    } catch {
      argsStr = String(o.arguments ?? '')
    }
    return { name: n, rest: `(${argsStr})` }
  }
  const c = typeof content === 'string' ? content : ''
  if (!c) return { name: 'tool', rest: '' }
  const idx = c.indexOf('(')
  if (idx > 0) return { name: c.slice(0, idx), rest: c.slice(idx) }
  return { name: c, rest: '' }
}
</script>
