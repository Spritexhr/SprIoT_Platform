export const DEFAULT_COLOR_THEME = 'apple'

const COLOR_THEME_KEY = 'iot-color-theme'
const SUPPORTED_COLOR_THEMES = new Set([DEFAULT_COLOR_THEME, 'classic'])

/**
 * 统一配色内部值，并兼容旧版 `claude` 存储值。
 */
export function normalizeColorTheme(value) {
  if (value === 'claude') return DEFAULT_COLOR_THEME
  return SUPPORTED_COLOR_THEMES.has(value) ? value : DEFAULT_COLOR_THEME
}

/**
 * 读取配色偏好；发现旧值或无效值时立即迁移，避免各处状态不一致。
 */
export function getStoredColorTheme() {
  const storedTheme = localStorage.getItem(COLOR_THEME_KEY)
  const colorTheme = normalizeColorTheme(storedTheme)
  if (storedTheme !== colorTheme) {
    localStorage.setItem(COLOR_THEME_KEY, colorTheme)
  }
  return colorTheme
}

/**
 * 应用配色 class，并清理旧版 Claude class。
 */
export function applyColorTheme(root, value) {
  const colorTheme = normalizeColorTheme(value)
  root.classList.remove('theme-apple', 'theme-classic', 'theme-claude')
  root.classList.add(`theme-${colorTheme}`)
  localStorage.setItem(COLOR_THEME_KEY, colorTheme)
  return colorTheme
}

/**
 * 初始化主题（在 Vue 挂载前调用，防止白屏闪烁）
 */
export function initTheme() {
  const root = document.documentElement

  // 亮暗模式
  const savedTheme = localStorage.getItem('iot-theme')
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  const theme = savedTheme || (prefersDark ? 'dark' : 'light')
  if (theme === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }
  if (!savedTheme) {
    localStorage.setItem('iot-theme', theme)
  }

  // 配色方案（旧版 claude 自动迁移为 apple）
  applyColorTheme(root, getStoredColorTheme())
}
