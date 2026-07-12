<template>
  <div class="kv-editor">
    <div v-if="!entries.length" class="config-empty">{{ emptyText }}</div>
    <div v-for="(entry, i) in entries" :key="i" class="header-row">
      <input
        class="config-input"
        type="text"
        spellcheck="false"
        autocomplete="off"
        :placeholder="keyPlaceholder"
        :value="entry.key"
        @change="renameKey(i, ($event.target as HTMLInputElement).value)"
      />
      <input
        class="config-input"
        type="text"
        spellcheck="false"
        autocomplete="off"
        :placeholder="valuePlaceholder"
        :value="entry.value"
        @input="updateValue(i, ($event.target as HTMLInputElement).value)"
      />
      <button type="button" class="btn-icon" title="删除" @click="removeAt(i)">✕</button>
    </div>
    <button type="button" class="btn-secondary btn-mini" @click="addEntry">＋ 添加</button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: Record<string, string>
  keyPlaceholder?: string
  valuePlaceholder?: string
  emptyText?: string
}>(), {
  keyPlaceholder: '键',
  valuePlaceholder: '值',
  emptyText: '暂无条目。',
})

const emit = defineEmits<{ 'update:modelValue': [value: Record<string, string>] }>()

const entries = computed(() => Object.entries(props.modelValue || {}).map(([key, value]) => ({ key, value })))

function addEntry(): void {
  const next = { ...props.modelValue }
  let k = 'key'
  let i = 1
  while (k in next) k = `key_${i++}`
  next[k] = ''
  emit('update:modelValue', next)
}

function removeAt(i: number): void {
  const next = { ...props.modelValue }
  delete next[entries.value[i].key]
  emit('update:modelValue', next)
}

function renameKey(i: number, newKey: string): void {
  const trimmed = newKey.trim()
  const oldKey = entries.value[i].key
  if (!trimmed || trimmed === oldKey) return
  const next: Record<string, string> = {}
  for (const [k, v] of Object.entries(props.modelValue)) {
    next[k === oldKey ? trimmed : k] = v
  }
  emit('update:modelValue', next)
}

function updateValue(i: number, value: string): void {
  const next = { ...props.modelValue, [entries.value[i].key]: value }
  emit('update:modelValue', next)
}
</script>
