<template>
  <div id="app">
    <div class="main-panel">
    <Sidebar
      :conversations="conversations"
      :active-id="activeId"
      :is-dark="isDark"
      :is-collapsed="sidebarCollapsed"
      @new="newConversation"
      @select="selectConversation"
      @delete="deleteConversation"
      @toggle-theme="toggleTheme"
      @toggle-collapse="toggleSidebar"
    />

      <div class="chat-wrapper">
        <div class="chat-area">
          <div class="chat-header">
            <span class="ws-dot" :class="activeId ? wsStatus : 'disconnected'"></span>
            <div class="conv-subtitle">{{ activeId ? wsStatusLabel : 'No conversation selected' }}</div>
            <div style="flex:1"></div>
          </div>

          <div class="messages" ref="messagesEl" :class="{ 'is-empty': !activeId }">
            <div v-if="activeId" class="messages-inner">
              <template v-for="(group, gi) in messageGroups" :key="gi">
                <ActionGroup
                  v-if="group.type === 'actions' && (!isLiveStreaming || gi < messageGroups.length - 1)"
                  :items="group.items"
                  :is-open="groupOpenState[group.key]"
                  @toggle="setGroupOpen(group.key, $event)"
                />
                <MessageBubble
                  v-else-if="group.type !== 'actions'"
                  :role="group.role"
                  :content="group.content"
                  :media="group.media"
                  @lightbox="openLightbox"
                />
              </template>

              <!-- Live streaming panel: renders items in arrival order, matching ActionGroup. -->
              <div v-if="isLiveStreaming" class="action-group">
                <details class="action-details" open>
                  <summary class="action-summary">
                    <span class="chevron">▶</span>
                    <span>{{ liveStreamLabel }}</span>
                    <span class="streaming-cursor"></span>
                  </summary>
                  <div class="action-items" ref="streamingItemsEl">
                    <template v-for="(item, idx) in streamingItems" :key="idx">
                      <div v-if="item.kind === 'thinking'" class="action-item is-progress">
                        {{ item.content }}
                      </div>
                      <div v-else-if="item.kind === 'tool_call'" class="action-item is-tool">
                        ⚙️ <span class="tool-name">{{ item.name }}</span><span class="tool-args">{{ item.arguments }}</span>
                      </div>
                      <div v-else-if="item.kind === 'content'" class="action-item is-progress">
                        <div class="reasoning-response" v-html="renderStreamingContent(item.content, idx === streamingItems.length - 1)"></div>
                      </div>
                    </template>
                  </div>
                </details>
              </div>

              <div v-if="isTyping && !isLiveStreaming" class="msg-row assistant">
                <div class="progress-card">
                  <div class="typing-dots"><span></span><span></span><span></span></div>
                  Thinking…
                </div>
              </div>
            </div>

            <EmptyState v-else @new="newConversation" />
          </div>

          <ChatInput
            v-model="inputText"
            :disabled="isTyping || !activeId"
            @send="onSend"
          />
        </div>
      </div>
    </div>

    <Lightbox :src="lightboxSrc" @close="lightboxSrc = null" />
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ActionGroup from './components/ActionGroup.vue'
import MessageBubble from './components/MessageBubble.vue'
import ChatInput from './components/ChatInput.vue'
import EmptyState from './components/EmptyState.vue'
import Lightbox from './components/Lightbox.vue'
import { useTheme } from './composables/useTheme.js'
import { useChat } from './composables/useChat.js'
import { renderMarkdown } from './utils/markdown.js'

const { isDark, toggleTheme } = useTheme()

const COLLAPSE_BREAKPOINT = 640

const sidebarCollapsed = ref(window.innerWidth < COLLAPSE_BREAKPOINT)
const userCollapsed = ref(false)

function toggleSidebar() {
  userCollapsed.value = !userCollapsed.value
  sidebarCollapsed.value = userCollapsed.value
}

function handleResize() {
  if (window.innerWidth < COLLAPSE_BREAKPOINT) {
    sidebarCollapsed.value = true
  } else {
    sidebarCollapsed.value = userCollapsed.value
  }
}

onMounted(() => window.addEventListener('resize', handleResize))
onUnmounted(() => window.removeEventListener('resize', handleResize))

const {
  conversations,
  activeId,
  inputText,
  isTyping,
  streamingContent,
  streamingThinking,
  streamingToolCallDeltas,
  streamingItems,
  wsStatus,
  wsStatusLabel,
  lightboxSrc,
  messagesEl,
  groupOpenState,
  setGroupOpen,
  messageGroups,
  newConversation,
  selectConversation,
  deleteConversation,
  sendMessage,
  sendCommand,
  openLightbox,
  autoResize,
  resetInputHeight,
} = useChat()

const streamingItemsEl = ref(null)

// Auto-scroll the live streaming panel to the bottom as content arrives.
watch(
  streamingItems,
  () => {
    nextTick(() => {
      if (streamingItemsEl.value) {
        streamingItemsEl.value.scrollTop = streamingItemsEl.value.scrollHeight
      }
    })
  },
  { deep: true }
)

function renderStreamingContent(content, isLast) {
  const html = renderMarkdown(content)
  return isLast ? html + '<span class="streaming-cursor"></span>' : html
}

const isLiveStreaming = computed(() =>
  streamingContent.value !== null ||
  streamingThinking.value !== null ||
  Object.keys(streamingToolCallDeltas.value).length > 0
)

const liveStreamLabel = computed(() => {
  const items = streamingItems.value
  const toolCalls = items.filter(i => i.kind === 'tool_call')
  if (toolCalls.length) {
    const names = toolCalls.map(i => i.name).filter(Boolean)
    return names.length ? `Calling ${names[names.length - 1]}…` : 'Running…'
  }
  if (items.some(i => i.kind === 'thinking')) return 'Thinking…'
  return 'Responding…'
})

function onSend() {
  sendMessage()
}
</script>
