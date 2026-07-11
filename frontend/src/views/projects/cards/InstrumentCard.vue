<template>
  <div class="instr-card" :class="statusClass" :title="title">
    <!-- 实时迷你折线图背景 -->
    <div v-if="history.length >= 2" class="instr-card__sparkline">
      <svg viewBox="0 0 120 30" preserveAspectRatio="none">
        <defs>
          <linearGradient :id="'sparkline-grad-' + tag" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" :stop-color="sparklineColor" stop-opacity="0.15" />
            <stop offset="100%" :stop-color="sparklineColor" stop-opacity="0.0" />
          </linearGradient>
        </defs>
        <path :d="sparklineSvg.area" :fill="'url(#sparkline-grad-' + tag + ')'" />
        <path :d="sparklineSvg.line" fill="none" :stroke="sparklineColor" stroke-width="1.2" />
      </svg>
    </div>

    <div class="instr-card__header">
      <span class="instr-card__name">{{ name }}</span>
      <span class="instr-card__online" :class="onlineClass">{{ onlineLabel }}</span>
    </div>

    <div v-if="description" class="instr-card__desc">{{ description }}</div>

    <div class="instr-card__value-row">
      <span class="instr-card__value">{{ displayValue }}</span>
      <span class="instr-card__unit">{{ unit }}</span>
    </div>

    <!-- 阈值水平刻度尺 -->
    <div v-if="hasThresholds" class="instr-card__threshold">
      <div class="threshold-track">
        <div class="threshold-active-zone" :style="{ left: loPercent + '%', width: (hiPercent - loPercent) + '%' }"></div>
        <div class="threshold-marker threshold-marker--low" :style="{ left: loPercent + '%' }"></div>
        <div class="threshold-marker threshold-marker--high" :style="{ left: hiPercent + '%' }"></div>
        <div class="threshold-pointer" :class="valueStatusClass" :style="{ left: currentPercent + '%' }"></div>
      </div>
      <div class="threshold-labels">
        <span class="threshold-label-text">L: {{ lo_threshold }}</span>
        <span class="threshold-label-text">H: {{ hi_threshold }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  sample: { type: Object, required: true },
  now:    { type: Number, default: () => Date.now() },
})

const tag         = computed(() => props.sample?.tag || '')
const name        = computed(() => props.sample?.metadata?.name || tag.value)
const description = computed(() => props.sample?.metadata?.description || '')
const unit        = computed(() => props.sample?.unit || '')
const status      = computed(() => props.sample?.status || 'normal')

// 优先采用后端基于 Sensor.last_seen 计算的统一在线状态（3 分钟）；
// 仅在没有主模型 Sensor 状态时，才按样本时间戳兜底。
const ONLINE_THRESHOLD_MS = 180_000
const isOnline = computed(() => {
  if (typeof props.sample?.is_online === 'boolean') return props.sample.is_online
  const ts = props.sample?.ts
  if (!ts) return null
  return (props.now - ts * 1000) < ONLINE_THRESHOLD_MS
})
const onlineLabel = computed(() => {
  if (isOnline.value === null) return '未知'
  return isOnline.value ? '在线' : '离线'
})
const onlineClass = computed(() => ({
  'instr-card__online--on':      isOnline.value === true,
  'instr-card__online--off':     isOnline.value === false,
  'instr-card__online--unknown': isOnline.value === null,
}))

const displayValue = computed(() => {
  const v = props.sample?.value
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  const abs = Math.abs(v)
  if (abs >= 100) return v.toFixed(1)
  if (abs >= 1)   return v.toFixed(2)
  return v.toFixed(3)
})

const statusClass = computed(() => ({
  'instr-card--warn':  status.value === 'warn_high'  || status.value === 'warn_low',
  'instr-card--alarm': status.value === 'alarm_high' || status.value === 'alarm_low',
}))

const title = computed(() => {
  const ts = props.sample?.ts ? new Date(props.sample.ts * 1000).toLocaleString() : '—'
  return `${tag.value}  ${name.value}\n${description.value ? description.value + '\n' : ''}最近更新: ${ts}`
})

// 记录本地滚动数据（最近 15 个点）
const history = ref([])
watch(() => props.sample?.value, (newVal) => {
  if (newVal !== null && newVal !== undefined && !Number.isNaN(newVal)) {
    history.value.push(newVal)
    if (history.value.length > 15) {
      history.value.shift()
    }
  }
}, { immediate: true })

// 渲染迷你 SVG
const sparklineSvg = computed(() => {
  const pts = history.value
  if (pts.length < 2) return { line: '', area: '' }
  const w = 120
  const h = 30
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const range = max - min === 0 ? 1 : max - min

  const coords = pts.map((val, idx) => {
    const x = (idx / (pts.length - 1)) * w
    const y = h - ((val - min) / range) * (h - 8) - 4 // 上下各留 4px padding
    return { x, y }
  })

  let linePath = `M ${coords[0].x} ${coords[0].y}`
  for (let i = 1; i < coords.length; i++) {
    const prev = coords[i - 1]
    const curr = coords[i]
    const cpX1 = prev.x + (curr.x - prev.x) / 3
    const cpY1 = prev.y
    const cpX2 = prev.x + 2 * (curr.x - prev.x) / 3
    const cpY2 = curr.y
    linePath += ` C ${cpX1} ${cpY1}, ${cpX2} ${cpY2}, ${curr.x} ${curr.y}`
  }

  const areaPath = `${linePath} L ${coords[coords.length - 1].x} ${h} L ${coords[0].x} ${h} Z`
  return { line: linePath, area: areaPath }
})

const sparklineColor = computed(() => {
  if (status.value === 'warn_high' || status.value === 'warn_low') return 'var(--iot-color-warning)'
  if (status.value === 'alarm_high' || status.value === 'alarm_low') return 'var(--iot-color-danger)'
  return 'var(--iot-color-primary)'
})

// 阈值计算
const hi_threshold = computed(() => props.sample?.hi_threshold)
const lo_threshold = computed(() => props.sample?.lo_threshold)
const hasThresholds = computed(() => {
  const hi = hi_threshold.value
  const lo = lo_threshold.value
  return hi !== null && hi !== undefined && lo !== null && lo !== undefined && hi > lo
})

const loPercent = 25
const hiPercent = 75
const currentPercent = computed(() => {
  const val = props.sample?.value
  if (val === null || val === undefined || Number.isNaN(val)) return 50
  const hi = hi_threshold.value
  const lo = lo_threshold.value
  let p = 25 + ((val - lo) / (hi - lo)) * 50
  return Math.max(0, Math.min(100, p))
})

const valueStatusClass = computed(() => {
  const val = props.sample?.value
  if (val === null || val === undefined || Number.isNaN(val)) return ''
  if (val > hi_threshold.value) return 'status-high'
  if (val < lo_threshold.value) return 'status-low'
  return 'status-normal'
})
</script>

<style scoped lang="scss">
.instr-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  background: var(--iot-bg-card);
  border: 1px solid var(--iot-border-color);
  border-radius: var(--iot-radius-base);
  transition: all var(--iot-transition-base);
  position: relative;
  overflow: hidden;
  z-index: 1;

  &:hover {
    border-color: var(--iot-color-primary);
    box-shadow: var(--iot-shadow-sm);
  }
}

.instr-card__sparkline {
  position: absolute;
  bottom: 0;
  left: 0;
  width: 100%;
  height: 38px;
  pointer-events: none;
  z-index: 0;
  opacity: 0.85;
}

.instr-card__header,
.instr-card__desc,
.instr-card__value-row,
.instr-card__threshold {
  position: relative;
  z-index: 1;
}

.instr-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.instr-card__name {
  font-size: var(--iot-font-size-sm);
  color: var(--iot-text-secondary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.instr-card__online {
  font-size: var(--iot-font-size-xs);
  padding: 1px 8px;
  border-radius: 20px;
  flex-shrink: 0;
  font-weight: 500;

  &--on      { background: var(--iot-color-success-bg); color: var(--iot-color-success); }
  &--off     { background: var(--iot-color-danger-bg); color: var(--iot-color-danger); }
  &--unknown { background: rgba(139, 123, 107, 0.10); color: var(--iot-text-secondary); }
}

.instr-card__desc {
  font-size: 11px;
  color: var(--iot-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: 0.8;
}

.instr-card__value-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-top: 4px;
}

.instr-card__value {
  font-size: var(--iot-font-size-xl);
  font-weight: 700;
  color: var(--iot-text-primary);
  font-variant-numeric: tabular-nums;
  font-family: var(--iot-font-mono, monospace);
}

.instr-card__unit {
  font-size: var(--iot-font-size-xs);
  color: var(--iot-text-secondary);
  font-weight: 500;
}

.instr-card--warn {
  background: var(--iot-color-warning-bg);
  border-color: var(--iot-color-warning);
}

.instr-card--alarm {
  background: var(--iot-color-danger-bg);
  border-color: var(--iot-color-danger);
  animation: alarm-pulse-heavy 1.5s ease-in-out infinite;

  .instr-card__value {
    color: var(--iot-color-danger);
  }
}

/* 阈值刻度 */
.instr-card__threshold {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--iot-border-color-light);

  .threshold-track {
    height: 4px;
    background: var(--iot-border-color-light);
    border-radius: 2px;
    position: relative;
    margin: 6px 0 4px;
  }

  .threshold-active-zone {
    position: absolute;
    height: 100%;
    background: rgba(76, 175, 130, 0.2);
    border-radius: 1px;
  }

  .threshold-marker {
    position: absolute;
    width: 2px;
    height: 6px;
    background: var(--iot-text-placeholder);
    top: -1px;
    transform: translateX(-50%);
  }

  .threshold-pointer {
    position: absolute;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    top: -2px;
    transform: translateX(-50%);
    background: var(--iot-color-primary);
    box-shadow: var(--iot-shadow-sm);
    transition: left 0.3s ease;

    &.status-high,
    &.status-low {
      background: var(--iot-color-danger);
      box-shadow: 0 0 6px var(--iot-color-danger);
    }

    &.status-normal {
      background: var(--iot-color-success);
    }
  }

  .threshold-labels {
    display: flex;
    justify-content: space-between;
    font-size: 9px;
    color: var(--iot-text-secondary);
    line-height: 1;
  }
}

@keyframes alarm-pulse-heavy {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(201, 74, 58, 0.15);
    border-color: var(--iot-color-danger);
  }
  50% {
    box-shadow: 0 0 12px 2px rgba(201, 74, 58, 0.35);
    border-color: var(--iot-color-danger-light);
  }
}
</style>
