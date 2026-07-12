<template>
  <div class="tag-list-input">
    <div class="tag-list-chips">
      <span v-for="(tag, i) in modelValue" :key="i" class="tag-chip">
        <span class="tag-chip-text">{{ tag }}</span>
        <button type="button" class="tag-chip-remove" title="删除" @click="removeAt(i)">✕</button>
      </span>
      <input
        v-model="draft"
        class="tag-list-add"
        type="text"
        spellcheck="false"
        autocomplete="off"
        :placeholder="modelValue.length ? '＋ 添加' : placeholder"
        @keydown.enter.prevent="commit"
        @keydown.backspace="onBackspace"
        @blur="commit"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: string[]
  placeholder?: string
}>(), {
  placeholder: '添加值…',
})

const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()

const draft = ref('')

function commit(): void {
  const v = draft.value.trim()
  if (!v) return
  emit('update:modelValue', [...props.modelValue, v])
  draft.value = ''
}

function removeAt(i: number): void {
  const next = props.modelValue.slice()
  next.splice(i, 1)
  emit('update:modelValue', next)
}

function onBackspace(): void {
  if (draft.value) return
  if (!props.modelValue.length) return
  removeAt(props.modelValue.length - 1)
}
</script>
