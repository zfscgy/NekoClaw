<template>
  <div class="cfg-tab">
    <section class="cfg-section">
      <div class="cfg-list-head">
        <span class="cfg-icon" style="--cfg-accent:#5fb89a">P</span>
        <span class="cfg-section-title">LLM 服务商</span>
        <span class="config-count">{{ providerNames.length }}</span>
        <span class="config-list-spacer"></span>
        <button type="button" class="btn-secondary btn-mini" @click="addProvider">＋ 添加服务商</button>
      </div>
      <p class="cfg-section-desc">OpenAI 兼容的 LLM 服务端点。模型 id 使用 <code>providerName/modelId</code> 形式引用某个 provider 下的模型。</p>

      <div v-if="!providerNames.length" class="cfg-empty-hint">还没有配置任何服务商，点击右上角添加一个吧。</div>

      <div v-else class="cfg-item-list">
        <div v-for="name in providerNames" :key="name" class="cfg-item-card">
          <div class="cfg-item-head">
            <input
              class="config-input cfg-item-name-input"
              type="text"
              spellcheck="false"
              autocomplete="off"
              :value="name"
              title="服务商名称"
              @change="renameProvider(name, ($event.target as HTMLInputElement).value)"
            />
            <button type="button" class="btn-icon" title="删除服务商" @click="removeProvider(name)">✕</button>
          </div>

          <div class="cfg-grid">
            <FieldRow label="API Base" hint="OpenAI 兼容的 Base URL，例如 https://api.openai.com/v1">
              <input
                class="config-input"
                type="text"
                spellcheck="false"
                autocomplete="off"
                :value="providers[name].api_base ?? ''"
                @input="setProviderOptional(name, 'api_base', ($event.target as HTMLInputElement).value)"
              />
            </FieldRow>
            <FieldRow label="API Key">
              <SecretInput :model-value="providers[name].api_key" @update:model-value="setProvider(name, 'api_key', $event)" />
            </FieldRow>
          </div>

          <FieldRow label="额外请求头" hint="额外请求头，例如某些网关要求的 APP-Code">
            <KeyValueEditor
              :model-value="providers[name].extra_headers ?? {}"
              key-placeholder="Header 名"
              value-placeholder="值"
              @update:model-value="setProvider(name, 'extra_headers', $event)"
            />
          </FieldRow>

          <div class="cfg-list-head">
            <span class="config-label">模型</span>
            <span class="config-count">{{ providers[name].models.length }}</span>
            <span class="config-list-spacer"></span>
            <button type="button" class="btn-secondary btn-mini" @click="addModel(name)">＋ 添加模型</button>
          </div>
          <div v-if="!providers[name].models.length" class="cfg-empty-hint">暂无模型，可手动添加或先填写 API Base 由系统推断推荐模型。</div>
          <div v-else class="cfg-item-list">
            <div v-for="(m, mi) in providers[name].models" :key="mi" class="cfg-model-card">
              <input
                class="config-input"
                type="text"
                spellcheck="false"
                autocomplete="off"
                placeholder="模型 ID"
                :value="m.id"
                @input="setModelField(name, mi, 'id', ($event.target as HTMLInputElement).value)"
              />
              <ToggleSwitch
                :model-value="m.image_input"
                label="支持图片输入"
                @update:model-value="setModelField(name, mi, 'image_input', $event)"
              />
              <ToggleSwitch
                :model-value="m.include_reasoning"
                label="回传推理内容"
                @update:model-value="setModelField(name, mi, 'include_reasoning', $event)"
              />
              <button type="button" class="btn-icon" title="删除模型" @click="removeModel(name, mi)">✕</button>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import FieldRow from './FieldRow.vue'
import SecretInput from './SecretInput.vue'
import KeyValueEditor from './KeyValueEditor.vue'
import ToggleSwitch from './ToggleSwitch.vue'
import type { Json } from '../../utils/configTypes'

interface ModelConfig {
  id: string
  image_input: boolean
  include_reasoning: boolean
}
interface ProviderConfig {
  api_key: string
  api_base: string | null
  extra_headers: Record<string, string> | null
  models: ModelConfig[]
}

const props = defineProps<{
  path: string
  value: Json
}>()

const emit = defineEmits<{ update: [path: string, value: Json] }>()

const rawOpenai = computed<Record<string, Partial<ProviderConfig>>>(() => {
  const v = props.value as { openai?: Record<string, Partial<ProviderConfig>> } | null
  return v?.openai ?? {}
})

const providerNames = computed(() => Object.keys(rawOpenai.value))

const providers = computed<Record<string, ProviderConfig>>(() => {
  const out: Record<string, ProviderConfig> = {}
  for (const [name, p] of Object.entries(rawOpenai.value)) {
    out[name] = {
      api_key: '', api_base: null, extra_headers: null, models: [],
      ...p,
    }
  }
  return out
})

function emitOpenai(next: Record<string, Partial<ProviderConfig>>): void {
  emit('update', `${props.path}.openai`, next as unknown as Json)
}

function addProvider(): void {
  const next = { ...rawOpenai.value }
  let name = 'provider'
  let i = 1
  while (name in next) name = `provider_${i++}`
  next[name] = { api_key: '', api_base: null, extra_headers: null, models: [] }
  emitOpenai(next)
}

function removeProvider(name: string): void {
  if (!window.confirm(`确定要删除 provider「${name}」吗？此操作会同时删除它下面配置的所有模型。`)) return
  const next = { ...rawOpenai.value }
  delete next[name]
  emitOpenai(next)
}

function renameProvider(oldName: string, newNameRaw: string): void {
  const newName = newNameRaw.trim()
  if (!newName || newName === oldName || newName in rawOpenai.value) return
  const next: Record<string, Partial<ProviderConfig>> = {}
  for (const [k, v] of Object.entries(rawOpenai.value)) {
    next[k === oldName ? newName : k] = v
  }
  emitOpenai(next)
}

function setProvider(name: string, field: keyof ProviderConfig, value: Json): void {
  const next = { ...rawOpenai.value, [name]: { ...providers.value[name], [field]: value } }
  emitOpenai(next)
}

function setProviderOptional(name: string, field: keyof ProviderConfig, raw: string): void {
  setProvider(name, field, raw === '' ? null : raw)
}

function addModel(name: string): void {
  const models = providers.value[name].models.slice()
  models.push({ id: '', image_input: true, include_reasoning: false })
  setProvider(name, 'models', models as unknown as Json)
}

function removeModel(name: string, i: number): void {
  const models = providers.value[name].models.slice()
  models.splice(i, 1)
  setProvider(name, 'models', models as unknown as Json)
}

function setModelField(name: string, i: number, field: keyof ModelConfig, value: Json): void {
  const models = providers.value[name].models.slice()
  models[i] = { ...models[i], [field]: value }
  setProvider(name, 'models', models as unknown as Json)
}
</script>
