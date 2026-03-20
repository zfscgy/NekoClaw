<template>
  <div class="empty-state">
    <span class="big-icon">🐈</span>
    <h2>Nanochat</h2>
    <p>Start a new conversation or select one from the sidebar.</p>
    <button class="btn-new" style="width:auto; padding: 10px 24px;" @click="$emit('new')">
      ＋ New conversation
    </button>
  </div>
</template>
