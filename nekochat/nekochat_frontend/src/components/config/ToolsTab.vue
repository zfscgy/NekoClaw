<template>
  <div class="cfg-tab">
    <section class="cfg-section">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#a78bd0">S</span>
        <span class="cfg-section-title">安全</span>
      </div>
      <ToggleSwitch
        :model-value="tools.restrict_to_workspace"
        label="限制所有工具访问只在工作区目录内"
        @update:model-value="setRoot('restrict_to_workspace', $event)"
      />
    </section>

    <section class="cfg-section">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#6f9bd0">W</span>
        <span class="cfg-section-title">Web 工具</span>
      </div>
      <div class="cfg-grid">
        <FieldRow label="代理" hint="HTTP/SOCKS5 代理地址，例如 socks5://127.0.0.1:1080">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="web.proxy ?? ''"
            @input="setWebOptional('proxy', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
        <FieldRow label="Chrome 可执行文件路径" hint="留空则使用系统自带浏览器">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="web.chrome_executable_path ?? ''"
            @input="setWebOptional('chrome_executable_path', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
        <FieldRow label="用户数据目录" hint="浏览器配置文件（cookies、登录态等）存放位置" class="cfg-span-2">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="web.user_data_dir"
            @input="setWeb('user_data_dir', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
      </div>
      <ToggleSwitch :model-value="web.headless" label="无头模式（不显示浏览器窗口）" @update:model-value="setWeb('headless', $event)" />

      <div class="cfg-subhead">搜索</div>
      <div class="cfg-grid cfg-grid--tight">
        <FieldRow label="最大结果数">
          <input
            class="config-input"
            type="number"
            min="1"
            step="1"
            :value="web.search.max_results"
            @input="setSearchMaxResults(($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
      </div>
      <div class="cfg-toggle-grid">
        <ToggleSwitch :model-value="web.search.engines.baidu" label="百度" @update:model-value="setEngine('baidu', $event)" />
        <ToggleSwitch :model-value="web.search.engines.google" label="Google" @update:model-value="setEngine('google', $event)" />
        <ToggleSwitch :model-value="web.search.engines.bing" label="Bing" @update:model-value="setEngine('bing', $event)" />
        <ToggleSwitch :model-value="web.search.engines.duckduckgo" label="DuckDuckGo" @update:model-value="setEngine('duckduckgo', $event)" />
      </div>
    </section>

    <section class="cfg-section">
      <div class="cfg-section-head">
        <span class="cfg-icon" style="--cfg-accent:#cf7f6f">E</span>
        <span class="cfg-section-title">Shell 执行工具</span>
      </div>
      <div class="cfg-grid cfg-grid--tight">
        <FieldRow label="超时（秒）">
          <input
            class="config-input"
            type="number"
            min="1"
            step="1"
            :value="exec.timeout"
            @input="setExecNumber('timeout', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
        <FieldRow label="PATH 追加" hint="追加到子进程 PATH 环境变量末尾">
          <input
            class="config-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :value="exec.path_append"
            @input="setExec('path_append', ($event.target as HTMLInputElement).value)"
          />
        </FieldRow>
      </div>
        <FieldRow label="Profile 文件" hint="启动 shell 时按顺序 source 的脚本文件">
          <TagListInput :model-value="exec.profile_files" placeholder="文件路径…" @update:model-value="setExec('profile_files', $event)" />
        </FieldRow>
        <FieldRow label="Profile 命令" hint="启动 shell 时额外执行的命令">
        <TagListInput :model-value="exec.profile_commands" placeholder="命令…" @update:model-value="setExec('profile_commands', $event)" />
      </FieldRow>
    </section>

    <section class="cfg-section">
      <div class="cfg-list-head">
        <span class="cfg-icon" style="--cfg-accent:#5fb89a">M</span>
        <span class="cfg-section-title">MCP 服务器</span>
        <span class="config-count">{{ mcpNames.length }}</span>
        <span class="config-list-spacer"></span>
        <button type="button" class="btn-secondary btn-mini" @click="addMcpServer">＋ 添加服务器</button>
      </div>

      <div v-if="!mcpNames.length" class="cfg-empty-hint">还没有配置 MCP 服务器。</div>
      <div v-else class="cfg-item-list">
        <div v-for="name in mcpNames" :key="name" class="cfg-item-card">
          <div class="cfg-item-head">
            <input
              class="config-input cfg-item-name-input"
              type="text"
              spellcheck="false"
              autocomplete="off"
              :value="name"
              title="服务器名称"
              @change="renameMcpServer(name, ($event.target as HTMLInputElement).value)"
            />
            <select
              class="config-input"
              style="flex: 0 0 150px"
              :value="mcpServers[name].type ?? ''"
              @change="setMcp(name, 'type', normalizeType(($event.target as HTMLSelectElement).value))"
            >
              <option value="">自动检测</option>
              <option value="stdio">stdio</option>
              <option value="sse">sse</option>
              <option value="streamableHttp">streamableHttp</option>
            </select>
            <button type="button" class="btn-icon" title="删除服务器" @click="removeMcpServer(name)">✕</button>
          </div>

          <div class="cfg-grid">
            <FieldRow label="命令" hint="stdio 方式：要运行的命令，例如 npx">
              <input
                class="config-input"
                type="text"
                spellcheck="false"
                autocomplete="off"
                :value="mcpServers[name].command"
                @input="setMcp(name, 'command', ($event.target as HTMLInputElement).value)"
              />
            </FieldRow>
            <FieldRow label="URL" hint="HTTP/SSE 方式：服务端点地址">
              <input
                class="config-input"
                type="text"
                spellcheck="false"
                autocomplete="off"
                :value="mcpServers[name].url"
                @input="setMcp(name, 'url', ($event.target as HTMLInputElement).value)"
              />
            </FieldRow>
            <FieldRow label="工具超时（秒）">
              <input
                class="config-input"
                type="number"
                min="1"
                step="1"
                :value="mcpServers[name].tool_timeout"
                @input="setMcpNumber(name, 'tool_timeout', ($event.target as HTMLInputElement).value)"
              />
            </FieldRow>
          </div>

          <FieldRow label="参数" hint="stdio 方式的命令行参数">
            <TagListInput :model-value="mcpServers[name].args" placeholder="参数…" @update:model-value="setMcp(name, 'args', $event)" />
          </FieldRow>
          <FieldRow label="环境变量" hint="stdio 方式的额外环境变量">
            <KeyValueEditor :model-value="mcpServers[name].env" key-placeholder="变量名" value-placeholder="值" @update:model-value="setMcp(name, 'env', $event)" />
          </FieldRow>
          <FieldRow label="请求头" hint="HTTP/SSE 方式的自定义请求头">
            <KeyValueEditor :model-value="mcpServers[name].headers" key-placeholder="Header 名" value-placeholder="值" @update:model-value="setMcp(name, 'headers', $event)" />
          </FieldRow>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import FieldRow from './FieldRow.vue'
import ToggleSwitch from './ToggleSwitch.vue'
import TagListInput from './TagListInput.vue'
import KeyValueEditor from './KeyValueEditor.vue'
import type { Json } from '../../utils/configTypes'

type McpType = 'stdio' | 'sse' | 'streamableHttp' | null

interface McpServerConfig {
  type: McpType
  command: string
  args: string[]
  env: Record<string, string>
  url: string
  headers: Record<string, string>
  tool_timeout: number
}

interface SearchEngines {
  baidu: boolean
  google: boolean
  bing: boolean
  duckduckgo: boolean
}
interface WebToolsConfig {
  proxy: string | null
  headless: boolean
  chrome_executable_path: string | null
  user_data_dir: string
  search: { max_results: number; engines: SearchEngines }
}
interface ExecToolConfig {
  timeout: number
  path_append: string
  profile_files: string[]
  profile_commands: string[]
}
interface ToolsConfig {
  web: Partial<WebToolsConfig>
  exec: Partial<ExecToolConfig>
  restrict_to_workspace: boolean
  mcp_servers: Record<string, Partial<McpServerConfig>>
}

const props = defineProps<{
  path: string
  value: Json
  modelOptions?: string[]
}>()

const emit = defineEmits<{ update: [path: string, value: Json] }>()

const tools = computed<ToolsConfig>(() => {
  const v = (props.value as Partial<ToolsConfig> | null) ?? {}
  return {
    web: v.web ?? {},
    exec: v.exec ?? {},
    restrict_to_workspace: v.restrict_to_workspace ?? false,
    mcp_servers: v.mcp_servers ?? {},
  }
})

const web = computed<WebToolsConfig>(() => ({
  proxy: null,
  headless: false,
  chrome_executable_path: null,
  user_data_dir: '',
  ...tools.value.web,
  search: {
    max_results: 20,
    ...(tools.value.web.search ?? {}),
    engines: {
      baidu: true, google: true, bing: true, duckduckgo: true,
      ...(tools.value.web.search?.engines ?? {}),
    },
  },
}))

const exec = computed<ExecToolConfig>(() => ({
  timeout: 30,
  path_append: '',
  profile_files: [],
  profile_commands: [],
  ...tools.value.exec,
}))

const mcpNames = computed(() => Object.keys(tools.value.mcp_servers))

const mcpServers = computed<Record<string, McpServerConfig>>(() => {
  const out: Record<string, McpServerConfig> = {}
  for (const [name, s] of Object.entries(tools.value.mcp_servers)) {
    out[name] = {
      type: null, command: '', args: [], env: {}, url: '', headers: {}, tool_timeout: 30,
      ...s,
    }
  }
  return out
})

function setRoot(field: keyof ToolsConfig, value: Json): void {
  emit('update', `${props.path}.${field}`, value)
}

function setWeb(field: keyof WebToolsConfig, value: Json): void {
  emit('update', `${props.path}.web.${field}`, value)
}

function setWebOptional(field: keyof WebToolsConfig, raw: string): void {
  setWeb(field, raw === '' ? null : raw)
}

function setSearchMaxResults(raw: string): void {
  const n = parseInt(raw, 10)
  emit('update', `${props.path}.web.search.max_results`, Number.isFinite(n) && n > 0 ? n : 1)
}

function setEngine(engine: keyof SearchEngines, value: boolean): void {
  emit('update', `${props.path}.web.search.engines.${engine}`, value)
}

function setExec(field: keyof ExecToolConfig, value: Json): void {
  emit('update', `${props.path}.exec.${field}`, value)
}

function setExecNumber(field: keyof ExecToolConfig, raw: string): void {
  const n = parseInt(raw, 10)
  setExec(field, Number.isFinite(n) && n > 0 ? n : 1)
}

function emitMcpServers(next: Record<string, Partial<McpServerConfig>>): void {
  emit('update', `${props.path}.mcp_servers`, next as unknown as Json)
}

function addMcpServer(): void {
  const next = { ...tools.value.mcp_servers }
  let name = 'server'
  let i = 1
  while (name in next) name = `server_${i++}`
  next[name] = { type: null, command: '', args: [], env: {}, url: '', headers: {}, tool_timeout: 30 }
  emitMcpServers(next)
}

function removeMcpServer(name: string): void {
  const next = { ...tools.value.mcp_servers }
  delete next[name]
  emitMcpServers(next)
}

function renameMcpServer(oldName: string, newNameRaw: string): void {
  const newName = newNameRaw.trim()
  if (!newName || newName === oldName || newName in tools.value.mcp_servers) return
  const next: Record<string, Partial<McpServerConfig>> = {}
  for (const [k, v] of Object.entries(tools.value.mcp_servers)) {
    next[k === oldName ? newName : k] = v
  }
  emitMcpServers(next)
}

function setMcp(name: string, field: keyof McpServerConfig, value: Json): void {
  const next = { ...tools.value.mcp_servers, [name]: { ...mcpServers.value[name], [field]: value } }
  emitMcpServers(next)
}

function setMcpNumber(name: string, field: keyof McpServerConfig, raw: string): void {
  const n = parseInt(raw, 10)
  setMcp(name, field, Number.isFinite(n) && n > 0 ? n : 1)
}

function normalizeType(raw: string): McpType {
  return raw === '' ? null : (raw as McpType)
}
</script>
