<template>
  <label class="toggle-switch" :class="{ disabled }">
    <input
      type="checkbox"
      :checked="modelValue"
      :disabled="disabled"
      @change="$emit('update:modelValue', ($event.target as HTMLInputElement).checked)"
    />
    <span class="toggle-track"><span class="toggle-thumb"></span></span>
    <span v-if="label" class="toggle-label">{{ label }}</span>
  </label>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  modelValue: boolean
  label?: string
  disabled?: boolean
}>(), {
  label: '',
  disabled: false,
})

defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>
