<template>
  <aside class="sidebar" :class="{ collapsed: isCollapsed }">
    <div class="sidebar-header">
      <template v-if="!isCollapsed">
        <img src="../assets/claw.png" class="logo-img" alt="NekoChat logo" />
        <h1>NekoChat</h1>
        <button
          class="btn-theme"
          :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
          @click="toggleTheme"
        >
          {{ isDark ? '☀️' : '🌙' }}
        </button>
      </template>
      <button class="btn-collapse" :title="isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'" @click="$emit('toggle-collapse')">
        <svg class="collapse-icon" :class="{ flipped: isCollapsed }" width="16" height="16" viewBox="0 0 16 16" fill="none"><polyline points="10,3 5,8 10,13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </div>
    <template v-if="!isCollapsed">
      <div style="padding: 0 8px;">
        <button class="btn-new" @click="newConversation">
          <span>＋</span> New conversation
        </button>
      </div>
      <div class="conv-list">
        <div
          v-for="c in conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: activeId === c.id }"
          @click="selectConversation(c.id)"
        >
          <span class="conv-preview">{{ c.preview || 'New conversation' }}</span>
          <button
            class="conv-delete"
            title="Delete"
            @click.stop="deleteConversation(c.id)"
          >
            ✕
          </button>
        </div>
      </div>
    </template>
    <template v-else>
      <div class="collapsed-actions">
        <button class="btn-new-icon" title="New conversation" @click="newConversation">＋</button>
        <button
          class="btn-theme"
          :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
          @click="toggleTheme"
        >
          {{ isDark ? '☀️' : '🌙' }}
        </button>
      </div>
    </template>
  </aside>
</template>

<script setup lang="ts">
import type { Conversation } from '../composables/useChat'

withDefaults(defineProps<{
  conversations: Conversation[]
  activeId?: string | null
  isDark: boolean
  isCollapsed?: boolean
}>(), {
  activeId: null,
  isCollapsed: false,
})

const emit = defineEmits<{
  new: []
  select: [id: string]
  delete: [id: string]
  'toggle-theme': []
  'toggle-collapse': []
}>()

function newConversation(): void { emit('new') }
function selectConversation(id: string): void { emit('select', id) }
function deleteConversation(id: string): void { emit('delete', id) }
function toggleTheme(): void { emit('toggle-theme') }
</script>
