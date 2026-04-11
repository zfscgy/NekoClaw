import { ref, onMounted } from 'vue'

export function useTheme() {
  const isDark = ref(localStorage.getItem('nekochat-theme') !== 'light')

  function applyTheme(): void {
    document.documentElement.classList.toggle('light', !isDark.value)
  }

  function toggleTheme(): void {
    isDark.value = !isDark.value
    localStorage.setItem('nekochat-theme', isDark.value ? 'dark' : 'light')
    applyTheme()
  }

  onMounted(applyTheme)
  return { isDark, toggleTheme }
}
