<template>
  <div v-if="src" class="lightbox" @click="$emit('close')">
    <img :src="src" alt="" />
  </div>
</template>

<script setup>
defineProps({ src: { type: String, default: null } })
defineEmits(['close'])
</script>
