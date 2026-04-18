<template>
  <div class="msg-row" :class="role">
    <div class="msg-bubble">
      <div v-if="role === 'assistant'" v-html="rendered"></div>
      <div v-else style="white-space: pre-wrap">{{ content }}</div>
      <div v-if="media?.length" class="msg-media">
        <template v-for="(m, mi) in media" :key="mi">
          <img
            v-if="isImage(m)"
            :src="mediaUrl(m)"
            :alt="m"
            loading="lazy"
            @click="$emit('lightbox', mediaUrl(m))"
          />
          <a v-else class="file-chip" :href="mediaUrl(m)" :download="fileName(m)">
            📎 {{ fileName(m) }}
          </a>
        </template>
      </div>
      <div v-if="formattedTime || streamStatus" class="msg-footer">
        <span v-if="formattedTime" class="msg-time">{{ formattedTime }}</span>
        <span v-if="streamStatus" class="stream-status-icon bubble-status" :class="streamStatus">
          <template v-if="streamStatus === 'complete'">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" xmlns="http://www.w3.org/2000/svg">
              <polyline points="1.5,5 3.8,7.5 8.5,2.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </template>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../utils/markdown'
import { isImage, mediaUrl, fileName } from '../utils/media'
import type { StreamStatus } from '../composables/useChat'

const props = withDefaults(defineProps<{
  role: string
  content?: string
  media?: string[]
  appendCursor?: boolean
  streamStatus?: StreamStatus
  time?: string
}>(), {
  content: '',
  media: () => [],
  appendCursor: false,
  streamStatus: undefined,
  time: undefined,
})

defineEmits<{ lightbox: [src: string] }>()

const rendered = computed(() =>
  renderMarkdown(props.content ?? '')
)

const formattedTime = computed(() => {
  if (!props.time) return null
  try {
    const d = new Date(props.time)
    if (isNaN(d.getTime())) return null
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return null
  }
})
</script>
