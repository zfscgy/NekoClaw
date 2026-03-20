<template>
  <aside class="sidebar" :class="{ collapsed: isCollapsed }">
    <div class="sidebar-header">
      <template v-if="!isCollapsed">
        <span class="logo">🐈</span>
        <h1>Nanochat</h1>
        <button
          class="btn-theme"
          :title="isDark ? 'Switch to light mode' : 'Switch to dark mode'"
          @click="toggleTheme"
        >
          {{ isDark ? '☀️' : '🌙' }}
        </button>
      </template>
      <button class="btn-collapse" :title="isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'" @click="$emit('toggle-collapse')">
        <span class="collapse-icon" :class="{ flipped: isCollapsed }">‹</span>
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

<script setup>
defineProps({
  conversations: { type: Array, required: true },
  activeId: { type: String, default: null },
  isDark: { type: Boolean, required: true },
  isCollapsed: { type: Boolean, default: false },
})

const emit = defineEmits(['new', 'select', 'delete', 'toggle-theme', 'toggle-collapse'])

function newConversation() { emit('new') }
function selectConversation(id) { emit('select', id) }
function deleteConversation(id) { emit('delete', id) }
function toggleTheme() { emit('toggle-theme') }
</script>
