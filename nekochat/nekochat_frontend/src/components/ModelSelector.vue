<template>
  <div class="model-selector" :class="{ open: dropdownOpen, saving }" ref="rootRef">
    <button
      type="button"
      class="model-trigger"
      :disabled="saving"
      :title="`模型：${current || '—'}`"
      @click="toggleDropdown"
    >
      <span class="model-trigger-text">{{ current || placeholder }}</span>
      <span class="model-caret" aria-hidden="true">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="2,4 5,7 8,4" />
        </svg>
      </span>
    </button>

    <div v-if="dropdownOpen" class="model-dropdown" @mousedown.prevent>
      <div v-if="error" class="model-error">{{ error }}</div>
      <input
        v-if="recommended.length"
        ref="searchRef"
        v-model="query"
        class="model-search"
        type="text"
        spellcheck="false"
        autocomplete="off"
        placeholder="筛选模型…"
        @keydown.enter.prevent="commitHighlighted"
        @keydown.escape="closeDropdown"
        @keydown.down.prevent="moveHighlight(1)"
        @keydown.up.prevent="moveHighlight(-1)"
      />
      <div v-if="filteredModels.length" class="model-options">
        <div
          v-for="(m, i) in filteredModels"
          :key="m"
          class="model-option"
          :class="{
            active: m === current,
            highlighted: i === highlight,
          }"
          @click="select(m)"
          @mouseenter="highlight = i"
        >
          <span class="model-option-id">{{ m }}</span>
          <span v-if="m === current" class="model-option-current">当前</span>
        </div>
      </div>
      <div v-else class="model-empty">
        {{ recommended.length ? '无匹配结果' : '还没有配置任何模型，请先在设置的“服务商”标签页中添加' }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'

const placeholder = '未选择模型'

interface ModelShape {
  id: string
  image_input?: boolean
  imageInput?: boolean
  include_reasoning?: boolean
  includeReasoning?: boolean
}

interface ProviderShape {
  models?: ModelShape[]
  api_base?: string | null
  apiBase?: string | null
}

interface ConfigShape {
  agents?: { defaults?: { model?: string } }
  // ``providers.openai`` is a map of unique provider name → provider config.
  providers?: { openai?: Record<string, ProviderShape> }
}

const current = ref<string>('')
const query = ref<string>('')
const recommended = ref<string[]>([])
const dropdownOpen = ref(false)
const error = ref<string | null>(null)
const saving = ref(false)
const highlight = ref(-1)

const rootRef = ref<HTMLElement | null>(null)
const searchRef = ref<HTMLInputElement | null>(null)

// Strictly a picker over configured models — never lets the user commit
// arbitrary/free-typed text as the active model. The search box only
// narrows `recommended`; selecting always goes through `select()`.
const filteredModels = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return recommended.value
  return recommended.value.filter(m => m.toLowerCase().includes(q))
})

async function load(): Promise<void> {
  try {
    const res = await fetch('/api/manager/config')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json() as { config: ConfigShape }
    const cfg = data.config || {}
    current.value = cfg.agents?.defaults?.model || ''
    // Build qualified ``providerName/modelId`` ids so the selected value names
    // both the provider and the model it belongs to.
    const merged: string[] = []
    const seen = new Set<string>()
    const openai = cfg.providers?.openai || {}
    for (const [name, prov] of Object.entries(openai)) {
      const list = prov?.models ?? []
      for (const m of list) {
        const id = m?.id
        if (!id) continue
        const qualified = `${name}/${id}`
        if (!seen.has(qualified)) { seen.add(qualified); merged.push(qualified) }
      }
    }
    recommended.value = merged
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function openDropdown(): void {
  dropdownOpen.value = true
  query.value = ''
  highlight.value = -1
  nextTick(() => searchRef.value?.focus())
}

function closeDropdown(): void {
  dropdownOpen.value = false
  query.value = ''
  highlight.value = -1
}

function toggleDropdown(): void {
  if (dropdownOpen.value) closeDropdown()
  else openDropdown()
}

function moveHighlight(delta: number): void {
  if (!filteredModels.value.length) return
  const len = filteredModels.value.length
  highlight.value = (highlight.value + delta + len) % len
}

function commitHighlighted(): void {
  if (highlight.value >= 0 && highlight.value < filteredModels.value.length) {
    select(filteredModels.value[highlight.value])
  } else if (filteredModels.value.length === 1) {
    select(filteredModels.value[0])
  }
}

async function select(model: string): Promise<void> {
  if (model === current.value) {
    closeDropdown()
    return
  }
  saving.value = true
  error.value = null
  try {
    const res = await fetch('/api/manager/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: 'agents.defaults.model', value: model }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { error?: string }
      throw new Error(body.error || `HTTP ${res.status}`)
    }
    current.value = model
    closeDropdown()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

function onDocClick(ev: MouseEvent): void {
  if (!rootRef.value) return
  if (!rootRef.value.contains(ev.target as Node)) closeDropdown()
}

onMounted(() => {
  load()
  document.addEventListener('mousedown', onDocClick)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onDocClick)
})

defineExpose({ refresh: load })
</script>
