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
        :class="[
          itemClass(item),
          { 'tool-call-clickable': item.type === 'tool_call' && !!item.toolResult },
        ]"
        :title="item.type === 'tool_call' && item.toolResult ? 'Click to view result' : ''"
        @click="item.type === 'tool_call' && item.toolResult && openResult(item)"
      >
        <ReasoningBlock v-if="item.type === 'think'" :content="item.content || ''" />
        <div v-else-if="item.type === 'tool_call'" class="subagent-tool">
          <span class="tool-call-card">
            <span class="tool-call-name">{{ toolCallDisplay(item).name }}</span>
            <span v-if="toolCallDisplay(item).argEntries.length" class="tool-call-args">
              <span
                v-for="(e, ei) in toolCallDisplay(item).argEntries"
                :key="ei"
                class="tool-call-arg"
              >
                <span class="tool-call-arg-key">{{ e.key }}</span>
                <span v-if="e.value || e.truncated" class="tool-call-arg-val">{{ e.value }}{{ e.truncated ? '…' : '' }}</span>
              </span>
            </span>
          </span>
        </div>
        <div v-else class="subagent-content" v-html="renderMarkdown(item.content || '')"></div>
      </div>
      <div v-if="agent.status === 'running' && !agent.items.length" class="subagent-thinking">
        <div class="typing-dots"><span></span><span></span><span></span></div>
        Working…
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="activeResult"
        class="modal-backdrop tool-result-backdrop"
        @click.self="closeResult"
      >
        <div class="modal-card tool-result-card">
          <div class="modal-header">
            <h2 class="modal-title">{{ activeResult.title }}</h2>
            <button class="modal-close" @click="closeResult" title="Close">✕</button>
          </div>
          <div class="modal-body tool-result-modal-body">
            <div class="tool-result-section">
              <div class="tool-result-section-label">参数</div>
              <div v-if="activeResult.argEntries.length" class="tool-arg-list">
                <div
                  v-for="(entry, ai) in activeResult.argEntries"
                  :key="ai"
                  class="tool-arg-item"
                >
                  <div class="tool-arg-key">{{ entry.key }}</div>
                  <pre class="tool-arg-value">{{ entry.value }}</pre>
                </div>
              </div>
              <div v-else class="tool-arg-empty">（无参数）</div>
            </div>
            <div class="tool-result-section">
              <div class="tool-result-section-label">结果</div>
              <pre class="tool-result-text">{{ activeResult.body }}</pre>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { renderMarkdown } from '../utils/markdown'
import type { SubagentState, ChatMessage } from '../composables/useChat'
import { toolCallDisplay, toolResultDetails, type ToolResultDetails } from '../utils/actions'
import ReasoningBlock from './ReasoningBlock.vue'

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
const activeResult = ref<ToolResultDetails | null>(null)

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

function openResult(item: ChatMessage): void {
  activeResult.value = toolResultDetails(item)
}

function closeResult(): void {
  activeResult.value = null
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && activeResult.value) closeResult()
}

onMounted(() => window.addEventListener('keydown', onKeyDown))
onUnmounted(() => window.removeEventListener('keydown', onKeyDown))
</script>
