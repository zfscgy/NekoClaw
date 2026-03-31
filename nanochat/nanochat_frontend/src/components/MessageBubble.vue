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
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../utils/markdown'
import { isImage, mediaUrl, fileName } from '../utils/media'

const props = withDefaults(defineProps<{
  role: string
  content?: string
  media?: string[]
  appendCursor?: boolean
}>(), {
  content: '',
  media: () => [],
  appendCursor: false,
})

defineEmits<{ lightbox: [src: string] }>()

const rendered = computed(() =>
  renderMarkdown(props.content ?? '') + (props.appendCursor ? '<span class="streaming-cursor"></span>' : '')
)
</script>
