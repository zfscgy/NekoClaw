<template>
  <div class="input-area">
    <div class="input-inner">
      <div class="input-row">
        <textarea
          ref="inputRef"
          class="input-box"
          v-model="model"
          placeholder="Message the agent…"
          rows="1"
          :disabled="disabled"
          @keydown.enter.exact.prevent="send"
          @keydown.enter.shift.exact="model += '\n'"
          @input="autoResize"
        />
        <button
          class="btn-send"
          :disabled="!model.trim() || disabled"
          title="Send (Enter)"
          @click="send"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M9 16V6.414L5.707 9.707a1 1 0 1 1-1.414-1.414l5-5 .076-.068a1 1 0 0 1 1.338.068l5 5 .068.076a1 1 0 0 1-1.406 1.406l-.076-.068L11 6.414V16a1 1 0 1 1-2 0"/></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'send'])

const model = ref(props.modelValue)
const inputRef = ref(null)

watch(() => props.modelValue, v => { model.value = v })
watch(model, v => emit('update:modelValue', v))
watch(() => props.modelValue, v => { if (!v) resetHeight() }, { flush: 'sync' })

function send() {
  if (!model.value.trim() || props.disabled) return
  emit('send')
}

function autoResize(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

function resetHeight() {
  if (inputRef.value) inputRef.value.style.height = 'auto'
}

defineExpose({ resetHeight })
</script>
