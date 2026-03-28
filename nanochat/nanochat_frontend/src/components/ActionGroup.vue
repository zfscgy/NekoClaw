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
        <span v-if="appendCursor" class="streaming-cursor"></span>
      </summary>
      <div class="action-items">
        <div
          v-for="(item, ii) in visible"
          :key="ii"
          class="action-item"
          :class="item.type === 'tool_call' ? 'is-tool' : 'is-progress'"
        >
          <span v-if="item.type === 'tool_call'">
            <span class="tool-name">{{ toolCallName(item.content) }}</span>{{ toolCallRest(item.content) }}
          </span>
          <div v-else-if="item.type === 'reasoning_response'" class="reasoning-response" v-html="renderMarkdown(item.content)"></div>
          <span v-else>{{ item.content }}</span>
        </div>
      </div>
    </details>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { toolCallName, toolCallRest, visibleItems, actionGroupLabel } from '../utils/actions'
import { renderMarkdown } from '../utils/markdown.js'

const props = defineProps({
  items: { type: Array, required: true },
  isOpen: { type: Boolean, required: true },
  appendCursor: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])

const visible = computed(() => visibleItems(props.items))
const label = computed(() => actionGroupLabel(props.items, visible.value))
</script>
