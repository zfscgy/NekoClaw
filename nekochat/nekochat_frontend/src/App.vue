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
            <button
              class="btn-header-icon"
              title="Skills"
              @click="showSkills = true"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2l2.09 6.26L20 9l-5 4.87L16.18 21 12 17.77 7.82 21 9 13.87 4 9l5.91-.74z"/>
              </svg>
            </button>
            <button
              class="btn-header-icon"
              title="Configuration"
              @click="showConfig = true"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="3"/>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
              </svg>
            </button>
          </div>

          <div class="messages" ref="messagesEl" :class="{ 'is-empty': !activeId }">
            <div v-if="activeId" class="messages-inner">
              <template v-for="(group, gi) in messageGroups" :key="gi">
                <ActionGroup
                  v-if="group.type === 'actions'"
                  :items="group.items"
                  :is-open="groupOpenState[group.key]"
                  :append-cursor="group.appendCursor"
                  :stream-status="group.streamStatus"
                  @toggle="setGroupOpen(group.key, $event)"
                />
                <SubagentLine
                  v-else-if="group.type === 'subagent_status'"
                  :label="group.label"
                  :event="group.event"
                  :status="group.status"
                  :report="group.report"
                  :task="group.task"
                  :append-cursor="group.appendCursor"
                  :stream-status="group.streamStatus"
                />
                <MessageBubble
                  v-else
                  :role="group.role"
                  :content="group.content"
                  :media="group.media"
                  :append-cursor="group.appendCursor"
                  :stream-status="group.streamStatus"
                  :time="group.time"
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
            :disabled="isSessionRunning || !activeId"
            :running="isSessionRunning"
            @send="onSend"
            @stop="stopGeneration"
          />
        </div>

        <div v-if="currentSubagents.length" class="subagent-panel" :class="{ collapsed: subagentPanelCollapsed }">
          <div class="subagent-panel-header">
            <button class="btn-collapse" @click="toggleSubagentPanel" :title="subagentPanelCollapsed ? 'Expand subagents' : 'Collapse subagents'">
              <svg class="collapse-icon" :class="{ flipped: subagentPanelCollapsed }" width="16" height="16" viewBox="0 0 16 16" fill="none"><polyline points="6,3 11,8 6,13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <template v-if="!subagentPanelCollapsed">
              <span class="subagent-panel-title">Subagents</span>
              <span class="subagent-panel-count">{{ currentSubagents.length }}</span>
            </template>
          </div>
          <div v-if="subagentPanelCollapsed" class="subagent-collapsed-badges">
            <div
              v-for="sub in currentSubagents"
              :key="sub.id"
              class="subagent-collapsed-dot"
              :class="sub.status"
              :title="sub.label"
            ></div>
          </div>
          <div v-else class="subagent-panel-list">
            <SubagentCard
              v-for="sub in currentSubagents"
              :key="sub.id"
              :agent="sub"
            />
          </div>
        </div>
      </div>
    </div>

    <Lightbox :src="lightboxSrc" @close="lightboxSrc = null" />
    <ConfigPanel v-if="showConfig" @close="showConfig = false" />
    <SkillsPanel v-if="showSkills" @close="showSkills = false" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ActionGroup from './components/ActionGroup.vue'
import MessageBubble from './components/MessageBubble.vue'
import SubagentLine from './components/SubagentLine.vue'
import ChatInput from './components/ChatInput.vue'
import EmptyState from './components/EmptyState.vue'
import Lightbox from './components/Lightbox.vue'
import SubagentCard from './components/SubagentCard.vue'
import ConfigPanel from './components/ConfigPanel.vue'
import SkillsPanel from './components/SkillsPanel.vue'
import { useTheme } from './composables/useTheme'
import { useChat } from './composables/useChat'

const { isDark, toggleTheme } = useTheme()

const SIDEBAR_COLLAPSE_BP = 640
const SUBAGENT_COLLAPSE_BP = 1100

const sidebarCollapsed = ref(window.innerWidth < SIDEBAR_COLLAPSE_BP)
const userCollapsed = ref(false)

const subagentPanelCollapsed = ref(window.innerWidth < SUBAGENT_COLLAPSE_BP)
const userSubagentCollapsed = ref(false)

const showConfig = ref(false)
const showSkills = ref(false)

function toggleSidebar() {
  userCollapsed.value = !userCollapsed.value
  sidebarCollapsed.value = userCollapsed.value
}

function toggleSubagentPanel() {
  const next = !subagentPanelCollapsed.value
  userSubagentCollapsed.value = next
  subagentPanelCollapsed.value = next
}

function handleResize() {
  if (window.innerWidth < SIDEBAR_COLLAPSE_BP) {
    sidebarCollapsed.value = true
  } else {
    sidebarCollapsed.value = userCollapsed.value
  }

  if (window.innerWidth < SUBAGENT_COLLAPSE_BP) {
    subagentPanelCollapsed.value = true
  } else {
    subagentPanelCollapsed.value = userSubagentCollapsed.value
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
  streamActive,
  wsStatus,
  wsStatusLabel,
  lightboxSrc,
  messagesEl,
  groupOpenState,
  setGroupOpen,
  messageGroups,
  currentSubagents,
  newConversation,
  selectConversation,
  deleteConversation,
  sendMessage,
  sendCommand,
  stopGeneration,
  openLightbox,
  autoResize,
} = useChat()

const isSessionRunning = computed(() => isTyping.value || streamActive.value)

function onSend(media: string[] = []) {
  sendMessage(media)
}
</script>
