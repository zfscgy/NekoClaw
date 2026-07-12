<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <div class="modal-card modal-wide config-modal">
      <div class="modal-header">
        <h2 class="modal-title">配置</h2>
        <button class="modal-close" @click="$emit('close')" title="关闭">✕</button>
      </div>

      <div v-if="loading" class="modal-body">
        <div class="modal-status">加载中…</div>
      </div>

      <template v-else>
        <div v-if="error" class="modal-error config-error-banner">{{ error }}</div>

        <div class="config-layout">
          <nav class="config-tabs" aria-label="配置分区">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              class="config-tab"
              :class="{ active: tab.key === activeTab }"
              @click="activeTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </nav>

          <div class="config-pane">
            <component
              :is="tabComponent"
              v-if="tabComponent"
              :path="activeTab"
              :value="draft[activeTab]"
              @update="onFieldUpdate"
            />
          </div>
        </div>
      </template>

      <div class="modal-footer">
        <button class="btn-secondary" @click="reset" :disabled="!isDirty || saving">
          放弃更改
        </button>
        <button class="btn-secondary" @click="$emit('close')">关闭</button>
        <button
          class="btn-primary"
          :disabled="loading || saving || !isDirty"
          @click="save"
        >
          {{ saving ? '保存中…' : isDirty ? `保存（${dirtyKeys.length}）` : '已保存' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, type Component } from 'vue'
import AgentsTab from './config/AgentsTab.vue'
import ChannelsTab from './config/ChannelsTab.vue'
import ProvidersTab from './config/ProvidersTab.vue'
import GatewayTab from './config/GatewayTab.vue'
import ToolsTab from './config/ToolsTab.vue'
import type { Json } from '../utils/configTypes'

defineEmits<{ close: [] }>()

const loading = ref(true)
const saving = ref(false)
const error = ref<string | null>(null)

const original = ref<Record<string, Json>>({})
const draft = ref<Record<string, Json>>({})
const activeTab = ref<string>('')

// Each top-level Config field gets a hand-built tab instead of a generic
// JSON-schema walker — see nekoclaw/config/schema.py for the source of truth.
const TAB_DEFS: { key: string; label: string; component: Component }[] = [
  { key: 'agents', label: 'Agent', component: AgentsTab },
  { key: 'channels', label: '频道', component: ChannelsTab },
  { key: 'providers', label: '服务商', component: ProvidersTab },
  { key: 'gateway', label: '网关', component: GatewayTab },
  { key: 'tools', label: '工具', component: ToolsTab },
]

const tabs = computed(() => TAB_DEFS.filter(t => t.key in draft.value))
const tabComponent = computed(() => TAB_DEFS.find(t => t.key === activeTab.value)?.component)

const dirtyKeys = computed(() => {
  const out: string[] = []
  for (const tab of tabs.value) {
    if (JSON.stringify(original.value[tab.key]) !== JSON.stringify(draft.value[tab.key])) {
      out.push(tab.key)
    }
  }
  return out
})

const isDirty = computed(() => dirtyKeys.value.length > 0)

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v ?? null))
}

function setByPath(root: Record<string, Json>, path: string, value: Json): void {
  const parts = path.split('.')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
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
    const data = await res.json() as { config: Record<string, Json> }
    original.value = data.config || {}
    draft.value = deepClone(data.config || {})

    const keys = TAB_DEFS.map(t => t.key).filter(k => k in draft.value)
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
