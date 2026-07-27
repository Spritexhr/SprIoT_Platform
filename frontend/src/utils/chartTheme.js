/**
 * 将主题色转换为指定透明度，供 ECharts 的线、面积和阴影复用。
 * 当前设计令牌使用 hex / rgb；无法解析时保留原值，避免渲染中断。
 */
export function withAlpha(color, alpha) {
  const value = String(color || '').trim()
  const hex = value.match(/^#([\da-f]{3}|[\da-f]{6}|[\da-f]{8})$/i)?.[1]
  if (hex) {
    const normalized = hex.length === 3
      ? hex.split('').map((part) => part + part).join('')
      : hex.slice(0, 6)
    const [r, g, b] = [0, 2, 4].map((offset) => Number.parseInt(normalized.slice(offset, offset + 2), 16))
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }

  const rgb = value.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i)
  if (rgb) return `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, ${alpha})`
  return value
}

/**
 * 每次渲染读取实时 CSS 令牌，使亮暗/配色切换无需维护第二套图表主题。
 */
export function readChartTheme() {
  const styles = getComputedStyle(document.documentElement)
  const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback
  const primary = token('--iot-color-primary', '#d96845')
  const success = token('--iot-color-success', '#27865f')
  const warning = token('--iot-color-warning', '#a86f00')
  const danger = token('--iot-color-danger', '#c83d36')
  const info = token('--iot-color-info', '#77716d')

  return {
    primary,
    warning,
    textPrimary: token('--iot-text-primary', '#201e1c'),
    textRegular: token('--iot-text-regular', '#47433f'),
    textSecondary: token('--iot-text-secondary', '#77716d'),
    textInverse: token('--iot-text-inverse', '#ffffff'),
    separator: token('--iot-separator', 'rgba(54, 48, 43, 0.095)'),
    border: token('--iot-border-color', 'rgba(54, 48, 43, 0.18)'),
    surface: token('--iot-bg-elevated', 'rgba(255, 255, 255, 0.92)'),
    material: token('--iot-material-thin', 'rgba(255, 255, 255, 0.56)'),
    fontFamily: token('--iot-font-family', '-apple-system, BlinkMacSystemFont, sans-serif'),
    radius: Number.parseFloat(token('--iot-radius-base', '12px')) || 12,
    palette: [
      primary,
      success,
      warning,
      danger,
      info,
      token('--iot-color-primary-light', '#e57d5b'),
      token('--iot-color-success-light', '#3ba278'),
    ],
  }
}
