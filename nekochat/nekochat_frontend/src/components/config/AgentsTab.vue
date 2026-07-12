<template>
  <div class="cfg-tab">
    <section class="cfg-section">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#c07090">W</span>
        <span class="cfg-section-title">工作区与模型</span>
      </div>
      <div class="cfg-grid">
        <FieldRow label="工作区" hint="Agent 文件操作的根目录">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="defaults.workspace"
            @input="set('workspace', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
        <FieldRow label="模型" hint="providerName/modelId 形式，例如 default/gpt-5.4；也可在顶部模型选择器中修改">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="defaults.model"
            @input="set('model', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
      </div>
    </section>

    <section class="cfg-section">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#6f9bd0">G</span>
        <span class="cfg-section-title">生成参数</span>
      </div>
      <div class="cfg-grid">
        <FieldRow label="最大 Token 数" hint="单次回复允许的最大 token 数">
          <input
            class="config-input"
            type="number"
            min="1"
            step="1"
            :value="defaults.max_tokens"
            @input="setNumber('max_tokens', $event, false)"
          />
        </FieldRow>
        <FieldRow label="温度" hint="采样温度，越高越随机（建议 0 ~ 2）">
          <input
            class="config-input"
            type="number"
            min="0"
            max="2"
            step="0.05"
            :value="defaults.temperature"
            @input="setNumber('temperature', $event, true)"
          />
        </FieldRow>
        <FieldRow label="推理强度" hint="启用模型思考模式的强度">
          <select
            class="config-input"
            :value="defaults.reasoning_effort ?? ''"
            @change="setReasoningEffort(($event.target as HTMLSelectElement).value)"
          >
            <option value="">关闭</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
          </select>
        </FieldRow>
        <FieldRow label="最大工具调用次数" hint="单轮对话中允许的最大工具调用次数">
          <input
            class="config-input"
            type="number"
            min="1"
            step="1"
            :value="defaults.max_tool_iterations"
            @input="setNumber('max_tool_iterations', $event, false)"
          />
        </FieldRow>
        <FieldRow label="记忆窗口" hint="保留在上下文窗口中的历史消息条数">
          <input
            class="config-input"
            type="number"
            min="1"
            step="1"
            :value="defaults.memory_window"
            @input="setNumber('memory_window', $event, false)"
          />
        </FieldRow>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import FieldRow from './FieldRow.vue'
import type { Json } from '../../utils/configTypes'

interface AgentDefaults {
  workspace: string
  model: string
  max_tokens: number
  temperature: number
  max_tool_iterations: number
  memory_window: number
  reasoning_effort: string | null
}

const props = defineProps<{
  path: string
  value: Json
}>()

const emit = defineEmits<{ update: [path: string, value: Json] }>()

const defaults = computed<AgentDefaults>(() => {
  const v = props.value as { defaults?: Partial<AgentDefaults> } | null
  return {
    workspace: '',
    model: '',
    max_tokens: 32768,
    temperature: 0.1,
    max_tool_iterations: 40,
    memory_window: 100,
    reasoning_effort: 'medium',
    ...(v?.defaults ?? {}),
  }
})

function set(field: string, value: Json): void {
  emit('update', `${props.path}.defaults.${field}`, value)
}

function setNumber(field: string, e: Event, float: boolean): void {
  const raw = (e.target as HTMLInputElement).value
  if (raw === '') { set(field, 0); return }
  const n = float ? parseFloat(raw) : parseInt(raw, 10)
  set(field, Number.isFinite(n) ? n : 0)
}

function setReasoningEffort(raw: string): void {
  set('reasoning_effort', raw === '' ? null : raw)
}
</script>
