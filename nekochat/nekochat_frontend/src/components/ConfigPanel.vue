<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <div class="modal-card modal-wide config-modal">
      <div class="modal-header">
        <h2 class="modal-title">Configuration</h2>
        <button class="modal-close" @click="$emit('close')" title="Close">✕</button>
      </div>

      <div v-if="loading" class="modal-body">
        <div class="modal-status">Loading…</div>
      </div>

      <template v-else>
        <div v-if="error" class="modal-error config-error-banner">{{ error }}</div>

        <div class="config-layout">
          <nav class="config-tabs" aria-label="Configuration sections">
            <button
              v-for="tab in tabs"
              :key="tab"
              class="config-tab"
              :class="{ active: tab === activeTab }"
              @click="activeTab = tab"
            >
              {{ prettyLabel(tab) }}
            </button>
          </nav>

          <div class="config-pane">
            <ConfigNode
              v-if="activeTab"
              :path="activeTab"
              :schema="schemaFor(activeTab)"
              :value="draft[activeTab]"
              :full-schema="fullSchema"
              @update="onFieldUpdate"
            />
          </div>
        </div>
      </template>

      <div class="modal-footer">
        <button class="btn-secondary" @click="reset" :disabled="!isDirty || saving">
          Reset
        </button>
        <button class="btn-secondary" @click="$emit('close')">Close</button>
        <button
          class="btn-primary"
          :disabled="loading || saving || !isDirty"
          @click="save"
        >
          {{ saving ? 'Saving…' : isDirty ? `Save (${dirtyKeys.length})` : 'Saved' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ConfigNode from './ConfigNode.vue'

type Json = string | number | boolean | null | Json[] | { [k: string]: Json }
interface SchemaNode {
  type?: string | string[]
  properties?: Record<string, SchemaNode>
  additionalProperties?: boolean | SchemaNode
  items?: SchemaNode
  anyOf?: SchemaNode[]
  oneOf?: SchemaNode[]
  enum?: Json[]
  default?: Json
  title?: string
  description?: string
  format?: string
}

defineEmits<{ close: [] }>()

const loading = ref(true)
const saving = ref(false)
const error = ref<string | null>(null)

const original = ref<Record<string, Json>>({})
const draft = ref<Record<string, Json>>({})
const fullSchema = ref<SchemaNode>({})
const tabs = ref<string[]>([])
const activeTab = ref<string>('')

const TAB_ORDER = ['agents', 'channels', 'providers', 'gateway', 'tools']

const dirtyKeys = computed(() => {
  const out: string[] = []
  for (const tab of tabs.value) {
    if (JSON.stringify(original.value[tab]) !== JSON.stringify(draft.value[tab])) {
      out.push(tab)
    }
  }
  return out
})

const isDirty = computed(() => dirtyKeys.value.length > 0)

function prettyLabel(key: string): string {
  const title = fullSchema.value.properties?.[key]?.title
  if (title) return title.replace(/Config$/, '') || title
  return key
}

function schemaFor(key: string): SchemaNode {
  return fullSchema.value.properties?.[key] ?? {}
}

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v ?? null))
}

function setByPath(root: Record<string, Json>, path: string, value: Json): void {
  const parts = path.split('.')
  let node: any = root
  for (let i = 0; i < parts.length - 1; i++) {
    if (node[parts[i]] == null || typeof node[parts[i]] !== 'object') {
      node[parts[i]] = {}
    }
    node = node[parts[i]]
  }
  node[parts[parts.length - 1]] = value
}

function onFieldUpdate(path: string, value: Json): void {
  setByPath(draft.value, path, value)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await fetch('/api/manager/config')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json() as { config: Record<string, Json>; schema: SchemaNode }
    original.value = data.config || {}
    draft.value = deepClone(data.config || {})
    fullSchema.value = data.schema || {}

    const keys = Object.keys(data.config || {})
    keys.sort((a, b) => {
      const ai = TAB_ORDER.indexOf(a)
      const bi = TAB_ORDER.indexOf(b)
      if (ai === -1 && bi === -1) return a.localeCompare(b)
      if (ai === -1) return 1
      if (bi === -1) return -1
      return ai - bi
    })
    tabs.value = keys
    if (!activeTab.value || !keys.includes(activeTab.value)) {
      activeTab.value = keys[0] ?? ''
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

function reset(): void {
  draft.value = deepClone(original.value)
}

async function save(): Promise<void> {
  saving.value = true
  error.value = null
  const changes = dirtyKeys.value.slice()
  try {
    for (const key of changes) {
      const res = await fetch('/api/manager/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value: draft.value[key] }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { error?: string }
        throw new Error(body.error || `HTTP ${res.status} on ${key}`)
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    saving.value = false
    return
  }
  saving.value = false
  await load()
}

onMounted(load)
</script>
