<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <div class="modal-card">
      <div class="modal-header">
        <h2 class="modal-title">Configuration</h2>
        <button class="modal-close" @click="$emit('close')" title="Close">✕</button>
      </div>

      <div class="modal-body">
        <div v-if="loading" class="modal-status">Loading…</div>
        <template v-else>
          <div v-if="error" class="modal-error">{{ error }}</div>

          <div class="config-section">
            <h3 class="config-section-title">OpenAI Provider</h3>
            <p class="config-hint">
              Credentials used to reach any OpenAI-compatible endpoint. Changes
              are saved to disk and applied to the running agent immediately.
            </p>

            <label class="config-field">
              <span class="config-label">API Key</span>
              <input
                v-model="apiKey"
                class="config-input"
                type="password"
                autocomplete="off"
                spellcheck="false"
                placeholder="sk-…"
              />
            </label>

            <label class="config-field">
              <span class="config-label">API Base URL</span>
              <input
                v-model="apiBase"
                class="config-input"
                type="text"
                spellcheck="false"
                placeholder="https://api.openai.com/v1"
              />
            </label>

            <div class="config-field">
              <span class="config-label">Extra Headers</span>
              <div class="header-list">
                <div v-for="(h, i) in extraHeaders" :key="i" class="header-row">
                  <input
                    class="config-input"
                    v-model="h.key"
                    placeholder="Header-Name"
                    spellcheck="false"
                  />
                  <input
                    class="config-input"
                    v-model="h.value"
                    placeholder="value"
                    spellcheck="false"
                  />
                  <button
                    class="btn-icon"
                    type="button"
                    title="Remove"
                    @click="removeHeader(i)"
                  >
                    ✕
                  </button>
                </div>
                <button type="button" class="btn-secondary" @click="addHeader">
                  ＋ Add header
                </button>
              </div>
            </div>
          </div>
        </template>
      </div>

      <div class="modal-footer">
        <button class="btn-secondary" @click="$emit('close')">Cancel</button>
        <button
          class="btn-primary"
          :disabled="loading || saving"
          @click="save"
        >
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface HeaderEntry { key: string; value: string }
interface ProviderConfig {
  api_key: string
  api_base: string
  extra_headers: Record<string, string>
}

defineEmits<{ close: [] }>()

const loading = ref(true)
const saving = ref(false)
const error = ref<string | null>(null)

const apiKey = ref('')
const apiBase = ref('')
const extraHeaders = ref<HeaderEntry[]>([])

function addHeader(): void {
  extraHeaders.value.push({ key: '', value: '' })
}

function removeHeader(i: number): void {
  extraHeaders.value.splice(i, 1)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await fetch('/api/manager/config/openai')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json() as { provider: ProviderConfig }
    apiKey.value = data.provider.api_key || ''
    apiBase.value = data.provider.api_base || ''
    extraHeaders.value = Object.entries(data.provider.extra_headers || {}).map(
      ([key, value]) => ({ key, value }),
    )
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  error.value = null
  try {
    const headerObj: Record<string, string> = {}
    for (const h of extraHeaders.value) {
      const k = h.key.trim()
      if (k) headerObj[k] = h.value
    }
    const res = await fetch('/api/manager/config/openai', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: apiKey.value,
        api_base: apiBase.value,
        extra_headers: headerObj,
      }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { error?: string }
      throw new Error(body.error || `HTTP ${res.status}`)
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
