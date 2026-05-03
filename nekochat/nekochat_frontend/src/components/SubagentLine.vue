<template>
  <div class="subagent-line-row">
    <button
      class="subagent-line"
      :class="[status, { clickable: hasDetail }]"
      :disabled="!hasDetail"
      :title="detailTitle"
      @click="openDetail"
    >
      <span class="subagent-line-rule"></span>
      <span class="subagent-line-dot" :class="status"></span>
      <span class="subagent-line-text">Subagent {{ label }} {{ statusText }}</span>
      <span v-if="streamStatus" class="stream-status-icon" :class="streamStatus">
        <template v-if="streamStatus === 'complete'">
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" xmlns="http://www.w3.org/2000/svg">
            <polyline points="1.5,5 3.8,7.5 8.5,2.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </template>
      </span>
      <span v-if="hasDetail" class="subagent-line-hint">{{ detailBadge }}</span>
      <span v-if="appendCursor" class="cursor-dot"></span>
      <span class="subagent-line-rule"></span>
    </button>

    <Teleport to="body">
      <div
        v-if="isOpen"
        class="modal-backdrop tool-result-backdrop"
        @click.self="closeDetail"
      >
        <div class="modal-card tool-result-card">
          <div class="modal-header">
            <h2 class="modal-title">{{ modalTitle }}</h2>
            <button class="modal-close" @click="closeDetail" title="Close">✕</button>
          </div>
          <div class="modal-body tool-result-modal-body">
            <div class="tool-result-section">
              <div class="tool-result-section-label">{{ detailLabel }}</div>
              <pre class="tool-result-text">{{ detailBody }}</pre>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { StreamStatus } from '../composables/useChat'

const props = defineProps<{
  label: string
  event: 'started' | 'finished'
  status: 'running' | 'ok' | 'error'
  report?: string
  task?: string
  appendCursor?: boolean
  streamStatus?: StreamStatus
}>()

const isOpen = ref(false)
const hasTask = computed(() => !!props.task?.trim())
const hasReport = computed(() => !!props.report?.trim())
const hasDetail = computed(() => props.event === 'started' ? hasTask.value : hasReport.value)
const statusText = computed(() => {
  if (props.event === 'started') return '开始执行'
  return props.status === 'error' ? '任务失败' : '任务完成'
})
const detailBadge = computed(() => props.event === 'started' ? '任务' : 'ReportTask')
const detailTitle = computed(() => {
  if (!hasDetail.value) return ''
  return props.event === 'started' ? 'Click to view assigned task' : 'Click to view ReportTask result'
})
const modalTitle = computed(() => (
  props.event === 'started'
    ? `Subagent ${props.label} 任务`
    : `Subagent ${props.label} ReportTask`
))
const detailLabel = computed(() => props.event === 'started' ? '主 Agent 下发任务' : '子 Agent ReportTask 结果')
const detailBody = computed(() => props.event === 'started' ? props.task : props.report)

function openDetail(): void {
  if (hasDetail.value) isOpen.value = true
}

function closeDetail(): void {
  isOpen.value = false
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && isOpen.value) closeDetail()
}

onMounted(() => window.addEventListener('keydown', onKeyDown))
onUnmounted(() => window.removeEventListener('keydown', onKeyDown))
</script>
