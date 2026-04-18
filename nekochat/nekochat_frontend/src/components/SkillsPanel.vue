<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <div class="modal-card modal-wide">
      <div class="modal-header">
        <h2 class="modal-title">Skills</h2>
        <div class="modal-header-actions">
          <input
            ref="fileInput"
            type="file"
            accept=".zip,application/zip,application/x-zip-compressed"
            class="file-input-hidden"
            @change="onFileChange"
          />
          <button
            class="btn-primary"
            :disabled="uploading"
            @click="fileInput?.click()"
          >
            {{ uploading ? 'Uploading…' : 'Upload skill (.zip)' }}
          </button>
          <button class="modal-close" @click="$emit('close')" title="Close">✕</button>
        </div>
      </div>

      <div class="modal-body">
        <div v-if="error" class="modal-error">{{ error }}</div>

        <div v-if="loading" class="modal-status">Loading…</div>
        <div v-else-if="!skills.length" class="modal-status">
          No skills installed yet. Upload a zipped skill to get started.
        </div>
        <div v-else class="skill-list">
          <div
            v-for="s in skills"
            :key="`${s.source}:${s.name}`"
            class="skill-card"
            :class="{ 'is-disabled': s.status === 'disabled' }"
          >
            <div class="skill-info">
              <div class="skill-head">
                <span class="skill-name">{{ s.name }}</span>
                <span
                  class="skill-badge"
                  :class="{
                    'is-builtin': s.source === 'builtin',
                    'is-off': s.status === 'disabled',
                  }"
                >
                  {{ s.source === 'builtin' ? 'builtin' : s.status }}
                </span>
              </div>
              <p v-if="s.description" class="skill-desc">{{ s.description }}</p>
            </div>
            <div class="skill-actions">
              <button
                v-if="s.editable && s.status === 'enabled'"
                class="btn-secondary"
                :disabled="!!pending[s.name]"
                @click="toggle(s, 'disable')"
              >
                {{ pending[s.name] === 'disable' ? 'Disabling…' : 'Disable' }}
              </button>
              <button
                v-else-if="s.editable && s.status === 'disabled'"
                class="btn-primary"
                :disabled="!!pending[s.name]"
                @click="toggle(s, 'enable')"
              >
                {{ pending[s.name] === 'enable' ? 'Enabling…' : 'Enable' }}
              </button>
              <span v-else class="skill-readonly">read-only</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface Skill {
  name: string
  source: 'workspace' | 'builtin'
  status: 'enabled' | 'disabled'
  description: string
  editable: boolean
}

defineEmits<{ close: [] }>()

const loading = ref(true)
const uploading = ref(false)
const error = ref<string | null>(null)
const skills = ref<Skill[]>([])
const pending = ref<Record<string, 'enable' | 'disable' | undefined>>({})
const fileInput = ref<HTMLInputElement | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await fetch('/api/manager/skills')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json() as { skills: Skill[] }
    skills.value = data.skills || []
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function toggle(skill: Skill, action: 'enable' | 'disable'): Promise<void> {
  pending.value = { ...pending.value, [skill.name]: action }
  error.value = null
  try {
    const res = await fetch(
      `/api/manager/skills/${encodeURIComponent(skill.name)}/${action}`,
      { method: 'POST' },
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { error?: string }
      throw new Error(body.error || `HTTP ${res.status}`)
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    const next = { ...pending.value }
    delete next[skill.name]
    pending.value = next
  }
  await load()
}

async function onFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  uploading.value = true
  error.value = null
  try {
    const form = new FormData()
    form.append('file', file, file.name)
    const res = await fetch('/api/manager/skills/upload', {
      method: 'POST',
      body: form,
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { error?: string }
      throw new Error(body.error || `HTTP ${res.status}`)
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    uploading.value = false
  }
  await load()
}

onMounted(load)
</script>
