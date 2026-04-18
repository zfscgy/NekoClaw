<template>
  <div class="input-area">
    <div class="input-inner">
      <!-- Attachment preview strip -->
      <div v-if="pendingFiles.length" class="attach-preview">
        <div
          v-for="f in pendingFiles"
          :key="f.id"
          class="attach-item"
          :class="{ 'is-image': f.isImage, 'is-uploading': f.uploading }"
        >
          <img v-if="f.isImage && f.previewUrl" :src="f.previewUrl" class="attach-thumb" />
          <span v-else class="attach-file-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
          </span>
          <span class="attach-name">{{ f.name }}</span>
          <span v-if="f.uploading" class="attach-spinner"></span>
          <button v-else class="attach-remove" @click="removeFile(f.id)" title="Remove">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/>
            </svg>
          </button>
        </div>
      </div>

      <div class="input-row">
        <!-- Hidden file input -->
        <input
          ref="fileInputRef"
          type="file"
          multiple
          class="file-input-hidden"
          @change="onFileChange"
        />
        <!-- Attach button -->
        <button
          class="btn-attach"
          :disabled="disabled"
          title="Attach file"
          @click="fileInputRef?.click()"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
          </svg>
        </button>

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
          :disabled="(!model.trim() && !readyFiles.length) || disabled || isUploading"
          title="Send (Enter)"
          @click="send"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M9 16V6.414L5.707 9.707a1 1 0 1 1-1.414-1.414l5-5 .076-.068a1 1 0 0 1 1.338.068l5 5 .068.076a1 1 0 0 1-1.406 1.406l-.076-.068L11 6.414V16a1 1 0 1 1-2 0"/></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'

interface PendingFile {
  id: number
  name: string
  isImage: boolean
  previewUrl: string   // blob URL for immediate preview; replaced with server URL on success
  serverUrl: string    // final /file/{token} URL (empty while uploading)
  uploading: boolean
}

const props = withDefaults(defineProps<{
  modelValue?: string
  disabled?: boolean
}>(), {
  modelValue: '',
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: [media: string[]]
}>()

const model = ref(props.modelValue)
const inputRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const pendingFiles = ref<PendingFile[]>([])
let _nextId = 0

const isUploading = computed(() => pendingFiles.value.some(f => f.uploading))
const readyFiles = computed(() => pendingFiles.value.filter(f => !f.uploading && f.serverUrl))

watch(() => props.modelValue, v => { model.value = v ?? '' })
watch(model, v => emit('update:modelValue', v))
watch(() => props.modelValue, v => { if (!v) resetHeight() }, { flush: 'sync' })

function send(): void {
  if ((!model.value.trim() && !readyFiles.value.length) || props.disabled || isUploading.value) return
  const media = readyFiles.value.map(f => f.serverUrl)
  emit('send', media)
  // Clean up blob URLs
  for (const f of pendingFiles.value) {
    if (f.previewUrl.startsWith('blob:')) URL.revokeObjectURL(f.previewUrl)
  }
  pendingFiles.value = []
  if (fileInputRef.value) fileInputRef.value.value = ''
}

function removeFile(id: number): void {
  const idx = pendingFiles.value.findIndex(f => f.id === id)
  if (idx === -1) return
  const f = pendingFiles.value[idx]
  if (f.previewUrl.startsWith('blob:')) URL.revokeObjectURL(f.previewUrl)
  pendingFiles.value.splice(idx, 1)
}

async function onFileChange(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  if (!input.files?.length) return
  const files = Array.from(input.files)
  // Reset so same file can be picked again
  input.value = ''

  for (const file of files) {
    const isImage = /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(file.name)
    const id = _nextId++
    const previewUrl = isImage ? URL.createObjectURL(file) : ''
    const entry: PendingFile = { id, name: file.name, isImage, previewUrl, serverUrl: '', uploading: true }
    pendingFiles.value.push(entry)
    uploadFile(file, id)
  }
}

async function uploadFile(file: File, id: number): Promise<void> {
  try {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch('/api/upload', { method: 'POST', body: fd })
    const data = await res.json() as { urls?: string[] }
    const serverUrl = data.urls?.[0] ?? ''

    const idx = pendingFiles.value.findIndex(f => f.id === id)
    if (idx === -1) return

    if (serverUrl) {
      pendingFiles.value[idx] = { ...pendingFiles.value[idx], serverUrl, uploading: false }
    } else {
      // Upload succeeded but no URL returned — remove
      const f = pendingFiles.value[idx]
      if (f.previewUrl.startsWith('blob:')) URL.revokeObjectURL(f.previewUrl)
      pendingFiles.value.splice(idx, 1)
    }
  } catch {
    const idx = pendingFiles.value.findIndex(f => f.id === id)
    if (idx !== -1) {
      const f = pendingFiles.value[idx]
      if (f.previewUrl.startsWith('blob:')) URL.revokeObjectURL(f.previewUrl)
      pendingFiles.value.splice(idx, 1)
    }
  }
}

function autoResize(e: Event): void {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

function resetHeight(): void {
  if (inputRef.value) inputRef.value.style.height = 'auto'
}

defineExpose({ resetHeight })
</script>
