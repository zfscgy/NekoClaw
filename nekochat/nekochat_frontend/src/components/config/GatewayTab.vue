<template>
  <div class="cfg-tab">
    <section class="cfg-section" :class="{ 'is-disabled': !heartbeat.enabled }">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#c9a45f">H</span>
        <span class="cfg-section-title">心跳</span>
        <ToggleSwitch :model-value="heartbeat.enabled" @update:model-value="set('enabled', $event)" />
      </div>
      <p class="cfg-section-desc">Agent 会按固定间隔主动“心跳”一次，用于保持长时任务的持续推进。</p>
      <div class="cfg-grid cfg-grid--tight">
        <FieldRow label="间隔（分钟）" hint="两次心跳之间的等待时间">
          <input
            class="config-input"
            type="number"
            min="1"
            step="1"
            :value="intervalMinutes"
            @input="setIntervalMinutes(($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import FieldRow from './FieldRow.vue'
import ToggleSwitch from './ToggleSwitch.vue'
import type { Json } from '../../utils/configTypes'

interface HeartbeatConfig {
  enabled: boolean
  interval_s: number
}

const props = defineProps<{
  path: string
  value: Json
  modelOptions?: string[]
}>()

const emit = defineEmits<{ update: [path: string, value: Json] }>()

const heartbeat = computed<HeartbeatConfig>(() => {
  const v = props.value as { heartbeat?: Partial<HeartbeatConfig> } | null
  return { enabled: true, interval_s: 1800, ...(v?.heartbeat ?? {}) }
})

const intervalMinutes = computed(() => Math.max(1, Math.round(heartbeat.value.interval_s / 60)))

function set(field: keyof HeartbeatConfig, value: Json): void {
  emit('update', `${props.path}.heartbeat.${field}`, value)
}

function setIntervalMinutes(raw: string): void {
  const n = parseInt(raw, 10)
  set('interval_s', (Number.isFinite(n) && n > 0 ? n : 1) * 60)
}
</script>
