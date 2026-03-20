import { ref, onMounted } from 'vue'

export function useTheme() {
  const isDark = ref(localStorage.getItem('nanochat-theme') !== 'light')

  function applyTheme() {
    document.documentElement.classList.toggle('light', !isDark.value)
  }

  function toggleTheme() {
    isDark.value = !isDark.value
    localStorage.setItem('nanochat-theme', isDark.value ? 'dark' : 'light')
    applyTheme()
  }

  onMounted(applyTheme)
  return { isDark, toggleTheme }
}
