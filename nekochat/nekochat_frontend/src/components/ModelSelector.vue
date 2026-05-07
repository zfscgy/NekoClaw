<template>
  <div class="model-selector" :class="{ open: dropdownOpen, dirty: isDirty, saving }" ref="rootRef">
    <input
      ref="inputRef"
      v-model="draft"
      class="model-input"
      type="text"
      spellcheck="false"
      autocomplete="off"
      :placeholder="placeholder"
      :title="`Model: ${current || '—'}`"
      @focus="openDropdown"
      @click="openDropdown"
      @keydown.enter.prevent="commit"
      @keydown.escape="cancelEdit"
      @keydown.down.prevent="moveHighlight(1)"
      @keydown.up.prevent="moveHighlight(-1)"
    />
    <button
      type="button"
      class="model-caret"
      :title="dropdownOpen ? 'Close' : 'Choose model'"
      @mousedown.prevent
      @click="toggleDropdown"
    >
      <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="2,4 5,7 8,4" />
      </svg>
    </button>

    <div v-if="dropdownOpen" class="model-dropdown" @mousedown.prevent>
      <div v-if="error" class="model-error">{{ error }}</div>
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
          <span v-if="m === current" class="model-option-current">current</span>
        </div>
      </div>
      <div v-else class="model-empty">
        {{ recommended.length ? 'No matches — press Enter to use as-is.' : 'No recommended models — type a model id and press Enter.' }}
      </div>
      <div class="model-hint">Enter to save · Esc to cancel</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

const PLACEHOLDER = 'Model id…'

interface ProviderShape {
  recommended_models?: string[]
  recommendedModels?: string[]
  api_base?: string | null
  apiBase?: string | null
}

interface ConfigShape {
  agents?: { defaults?: { model?: string } }
  providers?: Record<string, ProviderShape>
}

const placeholder = PLACEHOLDER

const current = ref<string>('')
const draft = ref<string>('')
const recommended = ref<string[]>([])
const dropdownOpen = ref(false)
const error = ref<string | null>(null)
const saving = ref(false)
const highlight = ref(-1)

const rootRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLInputElement | null>(null)

const isDirty = computed(() => draft.value.trim() !== current.value)

const filteredModels = computed(() => {
  const q = draft.value.trim().toLowerCase()
  // While the input still shows the unmodified current model, surface the
  // full recommended list — otherwise the current model id (which usually
  // doesn't appear in the recommended list as-is) would filter everything
  // out and leave the dropdown looking empty.
  if (!q || draft.value === current.value) return recommended.value
  return recommended.value.filter(m => m.toLowerCase().includes(q))
})

async function load(): Promise<void> {
  try {
    const res = await fetch('/api/manager/config')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json() as { config: ConfigShape }
    const cfg = data.config || {}
    current.value = cfg.agents?.defaults?.model || ''
    draft.value = current.value
    const merged: string[] = []
    const seen = new Set<string>()
    for (const prov of Object.values(cfg.providers || {})) {
      const list = prov?.recommended_models ?? prov?.recommendedModels ?? []
      for (const m of list) {
        if (m && !seen.has(m)) { seen.add(m); merged.push(m) }
      }
    }
    recommended.value = merged
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function openDropdown(): void {
  dropdownOpen.value = true
  highlight.value = -1
}

function closeDropdown(): void {
  dropdownOpen.value = false
  highlight.value = -1
}

function toggleDropdown(): void {
  if (dropdownOpen.value) {
    closeDropdown()
    inputRef.value?.blur()
  } else {
    openDropdown()
    inputRef.value?.focus()
  }
}

function moveHighlight(delta: number): void {
  if (!filteredModels.value.length) return
  if (!dropdownOpen.value) openDropdown()
  const len = filteredModels.value.length
  highlight.value = (highlight.value + delta + len) % len
}

function cancelEdit(): void {
  draft.value = current.value
  closeDropdown()
  inputRef.value?.blur()
}

async function select(model: string): Promise<void> {
  draft.value = model
  await commit()
}

async function commit(): Promise<void> {
  if (highlight.value >= 0 && highlight.value < filteredModels.value.length) {
    draft.value = filteredModels.value[highlight.value]
  }
  const next = draft.value.trim()
  if (!next) {
    cancelEdit()
    return
  }
  if (next === current.value) {
    closeDropdown()
    inputRef.value?.blur()
    return
  }

  saving.value = true
  error.value = null
  try {
    const res = await fetch('/api/manager/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: 'agents.defaults.model', value: next }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { error?: string }
      throw new Error(body.error || `HTTP ${res.status}`)
    }
    current.value = next
    draft.value = next
    closeDropdown()
    inputRef.value?.blur()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

function onDocClick(ev: MouseEvent): void {
  if (!rootRef.value) return
  if (!rootRef.value.contains(ev.target as Node)) {
    if (isDirty.value) draft.value = current.value
    closeDropdown()
  }
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
