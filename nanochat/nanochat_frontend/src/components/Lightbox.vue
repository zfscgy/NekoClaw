<template>
  <div v-if="src" class="lightbox" @click="$emit('close')">
    <img :src="src" alt="" />
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ src?: string | null }>(), { src: null })
defineEmits<{ close: [] }>()
</script>
