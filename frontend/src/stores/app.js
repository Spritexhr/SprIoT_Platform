import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import {
  applyColorTheme,
  getStoredColorTheme,
  normalizeColorTheme,
} from '@/utils/theme'

// 支持的配色方案: 'apple' | 'classic'（旧值 'claude' 会自动迁移）
// 亮暗模式: 'light' | 'dark'

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(false)
  const theme = ref(localStorage.getItem('iot-theme') || 'light')
  const colorTheme = ref(getStoredColorTheme())
  const sidebarDrawerVisible = ref(false)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function toggleTheme() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
  }

  function setTheme(newTheme) {
    theme.value = newTheme
  }

  function setColorTheme(name) {
    colorTheme.value = normalizeColorTheme(name)
  }

  function applyTheme() {
    const root = document.documentElement
    // 亮暗模式
    if (theme.value === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem('iot-theme', theme.value)

    // 配色方案
    applyColorTheme(root, colorTheme.value)
  }

  watch([theme, colorTheme], () => { applyTheme() }, { immediate: true, flush: 'sync' })

  return {
    sidebarCollapsed,
    theme,
    colorTheme,
    sidebarDrawerVisible,
    toggleSidebar,
    toggleTheme,
    setTheme,
    setColorTheme,
    applyTheme,
  }
})
