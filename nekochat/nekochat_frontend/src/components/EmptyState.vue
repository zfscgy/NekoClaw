<template>
  <div class="empty-state">
    <h2>NekoChat</h2>
    <p>Start a new conversation or select one from the sidebar.</p>
    <button class="btn-new" style="width:auto; padding: 10px 28px;" @click="$emit('new')">
      ＋ New conversation
    </button>
  </div>
</template>

<script setup lang="ts">
defineEmits<{ new: [] }>()
</script>
