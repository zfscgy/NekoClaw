<template>
  <div ref="el" class="reasoning-block" :class="{ 'is-markdown': markdown }">
    <div v-if="markdown" v-html="rendered"></div>
    <div v-else>{{ content }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { renderMarkdown } from '../utils/markdown'

const props = withDefaults(defineProps<{
  content: string
  markdown?: boolean
}>(), {
  markdown: false,
})

const el = ref<HTMLElement | null>(null)
const rendered = computed(() => renderMarkdown(props.content ?? ''))

// Reasoning grows tail-first while streaming, so keep it pinned to its own
// bottom (within the clamped height) unless the user has scrolled up inside
// this block to read earlier lines — mirrors the outer chat auto-scroll.
let userScrolledUp = false

function onScroll(): void {
  const node = el.value
  if (!node) return
  userScrolledUp = node.scrollHeight - node.scrollTop - node.clientHeight > 12
}

function pinToBottom(): void {
  nextTick(() => {
    const node = el.value
    if (!node || userScrolledUp) return
    node.scrollTop = node.scrollHeight
  })
}

watch(() => props.content, pinToBottom)

onMounted(() => {
  el.value?.addEventListener('scroll', onScroll, { passive: true })
  pinToBottom()
})
onUnmounted(() => {
  el.value?.removeEventListener('scroll', onScroll)
})
</script>
