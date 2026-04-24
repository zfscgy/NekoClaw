<template>
  <div class="action-group">
    <details
      class="action-details"
      :open="isOpen"
      @toggle="emit('toggle', $event.target?.open ?? isOpen)"
    >
      <summary class="action-summary">
        <span class="chevron">▶</span>
        <span>{{ isOpen ? 'Actions' : label }}</span>
        <span class="step-count">{{ visible.length }} step{{ visible.length === 1 ? '' : 's' }}</span>
        <span v-if="streamStatus" class="stream-status-icon" :class="streamStatus">
          <template v-if="streamStatus === 'complete'">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" xmlns="http://www.w3.org/2000/svg">
              <polyline points="1.5,5 3.8,7.5 8.5,2.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </template>
        </span>
      </summary>
      <div ref="actionItemsEl" class="action-items">
        <div
          v-for="(item, ii) in visible"
          :key="ii"
          class="action-item"
          :class="item.type === 'tool_call' ? 'is-tool' : 'is-progress'"
        >
          <span
            v-if="item.type === 'tool_call'"
            :class="{ 'tool-call-clickable': !!item.toolResult }"
            :title="item.toolResult ? 'Click to view result' : ''"
            @click="item.toolResult && openResult(item)"
          >
            <span class="tool-name">{{ toolCallName(item.content) }}</span>{{ toolCallRest(item.content) }}
          </span>
          <div v-else-if="item.type === 'reasoning_response'" class="reasoning-response" v-html="renderMarkdown(item.content)"></div>
          <span v-else>{{ item.content }}</span>
        </div>
      </div>
    </details>

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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { toolCallName, toolCallRest, visibleItems, actionGroupLabel, type ActionItem } from '../utils/actions'
import { renderMarkdown } from '../utils/markdown'
import type { StreamStatus } from '../composables/useChat'

const props = defineProps<{
  items: ActionItem[]
  isOpen: boolean
  appendCursor?: boolean
  streamStatus?: StreamStatus
}>()

const emit = defineEmits<{ toggle: [open: boolean] }>()

const visible = computed(() => visibleItems(props.items))
const label = computed(() => actionGroupLabel(props.items, visible.value))
const actionItemsEl = ref<HTMLElement | null>(null)

interface ToolArgEntry {
  key: string
  value: string
}

const activeResult = ref<{ title: string; argEntries: ToolArgEntry[]; body: string } | null>(null)

function formatArgValue(value: unknown): string {
  if (value == null) return String(value)
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function extractArgEntries(item: ActionItem): ToolArgEntry[] {
  const args = item.toolArguments
  if (args && typeof args === 'object') {
    return Object.entries(args).map(([k, v]) => ({ key: k, value: formatArgValue(v) }))
  }
  const rest = toolCallRest(item.content ?? '').trim()
  if (!rest || rest === '()') return []
  const inner = rest.startsWith('(') && rest.endsWith(')') ? rest.slice(1, -1).trim() : rest
  if (!inner) return []
  try {
    const parsed = JSON.parse(inner)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed))
      return Object.entries(parsed).map(([k, v]) => ({ key: k, value: formatArgValue(v) }))
  } catch { /* ignore */ }
  return [{ key: 'raw', value: inner }]
}

function openResult(item: ActionItem): void {
  if (!item.toolResult) return
  const name = item.toolName || toolCallName(item.content ?? '') || 'Tool'
  activeResult.value = {
    title: `${name} 执行结果`,
    argEntries: extractArgEntries(item),
    body: item.toolResult,
  }
}

function closeResult(): void {
  activeResult.value = null
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && activeResult.value) closeResult()
}

function scrollItemsToBottom(): void {
  nextTick(() => {
    if (!props.isOpen || !actionItemsEl.value) return
    actionItemsEl.value.scrollTop = actionItemsEl.value.scrollHeight
  })
}

watch(
  () => [visible.value.length, props.isOpen, props.appendCursor, props.streamStatus],
  scrollItemsToBottom,
  { flush: 'post' }
)

onMounted(() => {
  scrollItemsToBottom()
  window.addEventListener('keydown', onKeyDown)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeyDown)
})
</script>
