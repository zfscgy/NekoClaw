<template>
  <div class="cfg-tab">
    <!-- Telegram -->
    <section class="cfg-section" :class="{ 'is-disabled': !telegram.enabled }">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#2AABEE">T</span>
        <span class="cfg-section-title">Telegram</span>
        <ToggleSwitch :model-value="telegram.enabled" @update:model-value="set('telegram', 'enabled', $event)" />
      </div>
      <div class="cfg-grid">
        <FieldRow label="Bot Token" hint="从 @BotFather 获取">
          <SecretInput :model-value="telegram.token" @update:model-value="set('telegram', 'token', $event)" />
        </FieldRow>
        <FieldRow label="代理" hint="HTTP/SOCKS5 代理地址，例如 socks5://127.0.0.1:1080">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="telegram.proxy ?? ''"
            @input="setOptional('telegram', 'proxy', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
        <FieldRow label="允许访问" hint="允许访问的用户 ID / 用户名，留空表示不限制" class="cfg-span-2">
          <TagListInput :model-value="telegram.allow_from" placeholder="用户 ID 或用户名…" @update:model-value="set('telegram', 'allow_from', $event)" />
        </FieldRow>
      </div>
      <ToggleSwitch
        :model-value="telegram.reply_to_message"
        label="回复时引用原消息"
        @update:model-value="set('telegram', 'reply_to_message', $event)"
      />
    </section>

    <!-- QQ -->
    <section class="cfg-section" :class="{ 'is-disabled': !qq.enabled }">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#12B7F5">Q</span>
        <span class="cfg-section-title">QQ</span>
        <ToggleSwitch :model-value="qq.enabled" @update:model-value="set('qq', 'enabled', $event)" />
      </div>
      <div class="cfg-grid">
        <FieldRow label="App ID" hint="机器人 ID，来自 q.qq.com">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="qq.app_id"
            @input="set('qq', 'app_id', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
        <FieldRow label="App Secret" hint="机器人密钥，来自 q.qq.com">
          <SecretInput :model-value="qq.secret" @update:model-value="set('qq', 'secret', $event)" />
        </FieldRow>
        <FieldRow label="允许访问" hint="允许访问的 openid 列表，留空表示公开访问" class="cfg-span-2">
          <TagListInput :model-value="qq.allow_from" placeholder="openid…" @update:model-value="set('qq', 'allow_from', $event)" />
        </FieldRow>
      </div>
    </section>

    <!-- NekoChat -->
    <section class="cfg-section" :class="{ 'is-disabled': !nekochat.enabled }">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#c07090">N</span>
        <span class="cfg-section-title">NekoChat 网页端</span>
        <ToggleSwitch :model-value="nekochat.enabled" @update:model-value="set('nekochat', 'enabled', $event)" />
      </div>
      <div class="cfg-grid cfg-grid--tight">
        <FieldRow label="主机">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="nekochat.host"
            @input="set('nekochat', 'host', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
        <FieldRow label="端口">
          <input
            class="config-input"
            type="number"
            min="1"
            max="65535"
            step="1"
            :value="nekochat.port"
            @input="setPort(($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
        <FieldRow label="允许访问" hint='"*" 表示允许所有来源' class="cfg-span-2">
          <TagListInput :model-value="nekochat.allow_from" placeholder="来源…" @update:model-value="set('nekochat', 'allow_from', $event)" />
        </FieldRow>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import FieldRow from './FieldRow.vue'
import ToggleSwitch from './ToggleSwitch.vue'
import SecretInput from './SecretInput.vue'
import TagListInput from './TagListInput.vue'
import type { Json } from '../../utils/configTypes'

interface TelegramConfig {
  enabled: boolean
  token: string
  allow_from: string[]
  proxy: string | null
  reply_to_message: boolean
}
interface QQConfig {
  enabled: boolean
  app_id: string
  secret: string
  allow_from: string[]
}
interface NekoChatConfig {
  enabled: boolean
  host: string
  port: number
  allow_from: string[]
}
interface ChannelsValue {
  telegram?: Partial<TelegramConfig>
  qq?: Partial<QQConfig>
  nekochat?: Partial<NekoChatConfig>
}

const props = defineProps<{
  path: string
  value: Json
  modelOptions?: string[]
}>()

const emit = defineEmits<{ update: [path: string, value: Json] }>()

const raw = computed<ChannelsValue>(() => (props.value as ChannelsValue) || {})

const telegram = computed<TelegramConfig>(() => ({
  enabled: false, token: '', allow_from: [], proxy: null, reply_to_message: false,
  ...(raw.value.telegram ?? {}),
}))
const qq = computed<QQConfig>(() => ({
  enabled: false, app_id: '', secret: '', allow_from: [],
  ...(raw.value.qq ?? {}),
}))
const nekochat = computed<NekoChatConfig>(() => ({
  enabled: true, host: '127.0.0.1', port: 8899, allow_from: ['*'],
  ...(raw.value.nekochat ?? {}),
}))

function set(channel: string, field: string, value: Json): void {
  emit('update', `${props.path}.${channel}.${field}`, value)
}

function setOptional(channel: string, field: string, raw: string): void {
  set(channel, field, raw === '' ? null : raw)
}

function setPort(raw: string): void {
  const n = parseInt(raw, 10)
  set('nekochat', 'port', Number.isFinite(n) && n > 0 ? n : 8899)
}
</script>
