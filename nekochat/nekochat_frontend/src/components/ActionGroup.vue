<template>
  <div class="action-group">
    <details
      class="action-details"
      :open="isOpen"
      @toggle="emit('toggle', ($event.target as HTMLDetailsElement)?.open ?? isOpen)"
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
          :class="[
            item.type === 'tool_call' ? 'is-tool' : 'is-progress',
            { 'tool-call-clickable': item.type === 'tool_call' && !!item.toolResult },
          ]"
          :title="item.type === 'tool_call' && item.toolResult ? 'Click to view result' : ''"
          @click="item.type === 'tool_call' && item.toolResult && openResult(item)"
        >
          <span v-if="item.type === 'tool_call'" class="tool-call-inline">
            <span class="tool-bracket">[</span><span class="tool-name">{{ toolCallDisplay(item).name }}</span><span class="tool-bracket">]</span>
            <template v-if="toolCallDisplay(item).args"><span class="tool-bracket">[</span><span class="tool-args">{{ toolCallDisplay(item).args }}</span><span class="tool-bracket">]</span></template>
          </span>
          <ReasoningBlock v-else-if="item.type === 'reasoning_response'" :content="String(item.content ?? '')" markdown />
          <ReasoningBlock v-else :content="String(item.content ?? '')" />
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
import { toolCallDisplay, visibleItems, actionGroupLabel, toolResultDetails, type ActionItem, type ToolResultDetails } from '../utils/actions'
import ReasoningBlock from './ReasoningBlock.vue'
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
const activeResult = ref<ToolResultDetails | null>(null)

function openResult(item: ActionItem): void {
  activeResult.value = toolResultDetails(item)
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
