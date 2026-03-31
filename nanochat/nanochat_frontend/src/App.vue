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
                  v-if="group.type === 'actions'"
                  :items="group.items"
                  :is-open="groupOpenState[group.key]"
                  :append-cursor="group.appendCursor"
                  @toggle="setGroupOpen(group.key, $event)"
                />
                <MessageBubble
                  v-else
                  :role="group.role"
                  :content="group.content"
                  :media="group.media"
                  :append-cursor="group.appendCursor"
                  @lightbox="openLightbox"
                />
              </template>

              <div v-if="isTyping && !isStreaming" class="msg-row assistant">
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

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ActionGroup from './components/ActionGroup.vue'
import MessageBubble from './components/MessageBubble.vue'
import ChatInput from './components/ChatInput.vue'
import EmptyState from './components/EmptyState.vue'
import Lightbox from './components/Lightbox.vue'
import { useTheme } from './composables/useTheme'
import { useChat } from './composables/useChat'

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
  isStreaming,
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
} = useChat()

function onSend() {
  sendMessage()
}
</script>
