<template>
  <div class="config-field" :class="{ 'config-field-inline': inline }">
    <span v-if="label" class="config-label-wrap">
      <span class="config-label">{{ label }}</span>
      <span
        v-if="hint"
        ref="helpEl"
        class="cfg-help"
        tabindex="0"
        @mouseenter="showTip"
        @mouseleave="hideTip"
        @focus="showTip"
        @blur="hideTip"
      >
        <span class="cfg-help-icon" aria-hidden="true">?</span>
      </span>
    </span>
    <slot />

    <Teleport to="body">
      <div v-if="hint && tipVisible" class="cfg-help-tip-float" :style="tipStyle" role="tooltip">
        {{ hint }}
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'

withDefaults(defineProps<{
  label?: string
  hint?: string
  inline?: boolean
}>(), {
  label: '',
  hint: '',
  inline: false,
})

const TIP_WIDTH = 240
const VIEWPORT_MARGIN = 8

const helpEl = ref<HTMLElement | null>(null)
const tipVisible = ref(false)
const tipStyle = reactive({ top: '0px', left: '0px' })

function showTip(): void {
  const el = helpEl.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const left = Math.max(
    VIEWPORT_MARGIN,
    Math.min(rect.right - TIP_WIDTH, window.innerWidth - TIP_WIDTH - VIEWPORT_MARGIN),
  )
  tipStyle.left = `${left}px`
  tipStyle.top = `${rect.bottom + 7}px`
  tipVisible.value = true
}

function hideTip(): void {
  tipVisible.value = false
}
</script>
