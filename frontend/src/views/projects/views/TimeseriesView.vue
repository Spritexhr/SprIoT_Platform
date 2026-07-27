<template>
  <div class="ts">
    <div class="ts__bar">
      <div class="ts__selectors">
        <el-select v-model="selectedSource" placeholder="选择数据源" style="width: 240px" class="ts__select" @change="onSourceChange">
          <el-option-group label="传感器">
            <el-option
              v-for="s in sourceOptions.sensors"
              :key="`sensor:${s.id}`"
              :label="`${s.name} (${s.id})`"
              :value="`sensor:${s.id}`"
            />
          </el-option-group>
          <el-option-group label="设备">
            <el-option
              v-for="d in sourceOptions.devices"
              :key="`device:${d.id}`"
              :label="`${d.name} (${d.id})`"
              :value="`device:${d.id}`"
            />
          </el-option-group>
        </el-select>

        <el-select
          v-model="selectedFields"
          multiple
          collapse-tags
          collapse-tags-tooltip
          placeholder="选择监测指标"
          style="width: 220px"
          class="ts__select"
          :disabled="!availableFields.length"
        >
          <el-option v-for="f in availableFields" :key="f" :label="f" :value="f" />
        </el-select>

        <el-select v-model="quickRange" style="width: 120px" class="ts__select" @change="onQuickRangeChange">
          <el-option label="近 1 小时" :value="1" />
          <el-option label="近 6 小时" :value="6" />
          <el-option label="近 24 小时" :value="24" />
          <el-option label="近 7 天" :value="168" />
          <el-option label="自定义时间" value="custom" />
        </el-select>

        <el-date-picker
          v-if="quickRange === 'custom'"
          v-model="customRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          format="YYYY-MM-DD HH:mm"
          value-format="YYYY-MM-DDTHH:mm:ss"
          style="width: 340px"
          class="ts__picker"
        />
      </div>

      <div class="ts__actions">
        <el-button :loading="loading" size="small" @click="fetchSeries">查询刷新</el-button>
        <el-button v-if="canEdit" size="small" type="primary" plain :loading="saving" @click="saveDefault">
          保存为默认视图
        </el-button>
      </div>
    </div>

    <div v-loading="loading" class="ts__chart-wrap">
      <div v-if="!selectedSource" class="ts__empty">
        <el-empty description="请在上方选择一个点位数据源开始监测" />
      </div>
      <div v-else-if="!hasPoints && !loading" class="ts__empty">
        <el-empty description="当前时间窗口内暂无历史遥测数据" />
      </div>
      <div v-show="hasPoints" ref="chartEl" class="ts__chart" />
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'

import { getProjectSeries, updateView } from '@/api/projects'
import { useProjectStore } from '@/stores/project'
import { readChartTheme, withAlpha } from '@/utils/chartTheme'

const props = defineProps({
  view: { type: Object, required: true },
  canEdit: { type: Boolean, default: false },
})
const emit = defineEmits(['saved'])

const store = useProjectStore()

const selectedSource = ref('')
const selectedFields = ref([])
const quickRange = ref(24)
const customRange = ref([])
const seriesData = ref({ points: [], events: [], fields: [], count: 0, truncated: false })
const loading = ref(false)
const saving = ref(false)

const chartEl = ref(null)
let chartInstance = null

const hasPoints = computed(() => (seriesData.value.points || []).length > 0)
const availableFields = computed(() => seriesData.value.fields || [])

const sourceOptions = computed(() => {
  const sensors = new Map()
  const devices = new Map()
  const secs = (store.layout?.sections || []).filter(
    (sec) => props.view.section == null || sec.id === props.view.section,
  )
  for (const sec of secs) {
    for (const s of sec.sensors || []) {
      if (s.sensor_id && !sensors.has(s.sensor_id)) sensors.set(s.sensor_id, { id: s.sensor_id, name: s.sensor_name || s.tag })
    }
    for (const d of sec.devices || []) {
      if (d.device_id && !devices.has(d.device_id)) devices.set(d.device_id, { id: d.device_id, name: d.device_name || d.tag })
    }
  }
  return { sensors: [...sensors.values()], devices: [...devices.values()] }
})

function getRange() {
  if (quickRange.value === 'custom') {
    if (!customRange.value || customRange.value.length !== 2) return null
    return { start: customRange.value[0], end: customRange.value[1] }
  }
  const end = new Date()
  const start = new Date(end.getTime() - quickRange.value * 3600 * 1000)
  return { start: start.toISOString(), end: end.toISOString() }
}

function onSourceChange() {
  selectedFields.value = []
  fetchSeries()
}
function onQuickRangeChange() {
  if (quickRange.value !== 'custom') fetchSeries()
}

async function fetchSeries() {
  if (!selectedSource.value) return
  const range = getRange()
  if (!range) { ElMessage.warning('请选择有效时间范围'); return }
  const [kind, sourceId] = selectedSource.value.split(':')
  loading.value = true
  try {
    const res = await getProjectSeries(props.view.project ?? store.currentProjectId, {
      kind, sourceId, start: range.start, end: range.end,
    })
    seriesData.value = res
    if (!selectedFields.value.length) {
      selectedFields.value = (res.fields || []).slice(0, 2)
    }
    await nextTick()
    renderChart()
  } catch (e) {
    ElMessage.error('获取数据失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function coerceNumber(v) {
  if (v === null || v === undefined) return null
  if (typeof v === 'boolean') return v ? 1 : 0
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function renderChart() {
  if (!chartEl.value) return
  if (!chartInstance) chartInstance = echarts.init(chartEl.value)
  const theme = readChartTheme()
  
  const points = seriesData.value.points || []
  const events = seriesData.value.events || []
  const fields = selectedFields.value.length ? selectedFields.value : availableFields.value.slice(0, 2)

  const series = fields.map((field, idx) => {
    const color = theme.palette[idx % theme.palette.length]
    return {
      name: field,
      type: 'line',
      smooth: true,
      showSymbol: points.length < 200,
      connectNulls: false,
      sampling: 'lttb',
      itemStyle: { color },
      lineStyle: { color, width: 2.5 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: withAlpha(color, 0.14) },
          { offset: 1, color: withAlpha(color, 0) },
        ]),
      },
      emphasis: { focus: 'series' },
      data: points.map((p) => [p.t, coerceNumber(p.data?.[field])]),
    }
  })

  const visibleEvents = events.filter((e) => e.event !== 'heartbeat')
  if (visibleEvents.length && series.length) {
    series[0] = {
      ...series[0],
      markLine: {
        symbol: 'none',
        silent: true,
        lineStyle: { color: withAlpha(theme.warning, 0.62), type: 'dashed' },
        data: visibleEvents.slice(0, 50).map((e) => ({
          xAxis: e.t,
          label: {
            formatter: e.event || '',
            position: 'end',
            fontSize: 9,
            color: theme.warning,
          },
        })),
      },
    }
  }

  chartInstance.setOption({
    backgroundColor: 'transparent',
    color: theme.palette,
    textStyle: {
      fontFamily: theme.fontFamily,
      color: theme.textRegular,
    },
    tooltip: { 
      trigger: 'axis',
      confine: true,
      backgroundColor: theme.surface,
      borderColor: theme.border,
      borderWidth: 1,
      padding: 12,
      textStyle: { color: theme.textPrimary, fontFamily: theme.fontFamily },
      axisPointer: {
        type: 'cross',
        lineStyle: { color: withAlpha(theme.primary, 0.52) },
        crossStyle: { color: withAlpha(theme.primary, 0.52) },
        label: { color: theme.textInverse, backgroundColor: theme.primary },
      },
      extraCssText: `border-radius:${theme.radius}px;box-shadow:0 12px 32px ${withAlpha(theme.textPrimary, 0.14)};backdrop-filter:blur(18px);`,
    },
    legend: { 
      data: fields, 
      top: 0,
      textStyle: { color: theme.textRegular, fontFamily: theme.fontFamily },
    },
    grid: { left: 40, right: 40, top: 65, bottom: 85, containLabel: true },
    xAxis: { 
      type: 'time', 
      axisLine: { lineStyle: { color: theme.separator } },
      axisTick: { lineStyle: { color: theme.separator } },
      axisLabel: { hideOverlap: true, color: theme.textSecondary },
      splitLine: { show: true, lineStyle: { color: theme.separator } },
    },
    yAxis: { 
      type: 'value', 
      scale: true,
      axisLine: { lineStyle: { color: theme.separator } },
      axisTick: { lineStyle: { color: theme.separator } },
      axisLabel: { color: theme.textSecondary },
      splitLine: { show: true, lineStyle: { color: theme.separator } },
    },
    dataZoom: [
      { type: 'inside' }, 
      { 
        type: 'slider', 
        height: 20, 
        bottom: 10,
        textStyle: { color: theme.textSecondary },
        borderColor: theme.separator,
        backgroundColor: theme.material,
        fillerColor: withAlpha(theme.primary, 0.14),
        dataBackground: {
          lineStyle: { color: withAlpha(theme.textSecondary, 0.32) },
          areaStyle: { color: withAlpha(theme.textSecondary, 0.08) },
        },
        selectedDataBackground: {
          lineStyle: { color: withAlpha(theme.primary, 0.64) },
          areaStyle: { color: withAlpha(theme.primary, 0.12) },
        },
        handleStyle: { color: theme.primary, borderColor: theme.surface },
        moveHandleStyle: { color: theme.primary },
      },
    ],
    series,
  }, { notMerge: true })
}

function handleResize() { chartInstance?.resize() }

async function saveDefault() {
  saving.value = true
  try {
    const config = {
      source: selectedSource.value,
      fields: selectedFields.value,
      quickRange: quickRange.value,
    }
    await updateView(props.view.id, { config })
    emit('saved', { id: props.view.id, config })
    ElMessage.success('已保存为默认')
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

let themeObserver = null
let themeFrame = null
let resizeObserver = null
onMounted(() => {
  window.addEventListener('resize', handleResize)
  
  // 根节点主题类变化后，在样式完成计算的下一帧刷新整套图表颜色。
  themeObserver = new MutationObserver(() => {
    if (themeFrame) cancelAnimationFrame(themeFrame)
    themeFrame = requestAnimationFrame(() => {
      themeFrame = null
      if (chartInstance && hasPoints.value) renderChart()
    })
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  
  // 观察图表容器尺寸变化（解决在隐藏 tab 切换时 ECharts 宽度坍塌的经典 Bug）
  if (chartEl.value) {
    resizeObserver = new ResizeObserver(() => {
      if (chartEl.value.clientWidth > 0) {
        handleResize()
      }
    })
    resizeObserver.observe(chartEl.value)
  }
  
  // 从 config 恢复默认选择
  const cfg = props.view.config || {}
  if (cfg.source) {
    selectedSource.value = cfg.source
    if (Array.isArray(cfg.fields)) selectedFields.value = cfg.fields
    if (cfg.quickRange) quickRange.value = cfg.quickRange
    fetchSeries()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (themeObserver) {
    themeObserver.disconnect()
  }
  if (themeFrame) {
    cancelAnimationFrame(themeFrame)
    themeFrame = null
  }
  if (resizeObserver) {
    resizeObserver.disconnect()
  }
  chartInstance?.dispose()
  chartInstance = null
})

watch(selectedFields, () => {
  if (selectedSource.value && hasPoints.value) renderChart()
})
</script>

<style scoped lang="scss">
.ts {
  display: flex;
  flex-direction: column;
  background: var(--iot-bg-card);
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-lg);
  box-shadow: var(--iot-shadow-sm);
  padding: var(--iot-spacing-lg);
  animation: iot-fade-in 0.4s ease forwards;
}

.ts__bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--iot-spacing-md);
  flex-wrap: wrap;
  gap: var(--iot-spacing-md);
}

.ts__selectors {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.ts__actions {
  display: flex;
  gap: 8px;
}

.ts__chart-wrap {
  position: relative;
  min-height: 480px;
  border-top: 1px solid var(--iot-border-color-light);
  padding-top: var(--iot-spacing-md);
}

.ts__chart {
  width: 100%;
  height: 480px;
}

.ts__empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--iot-text-placeholder);
  font-size: var(--iot-font-size-base);
}

@media screen and (max-width: 767px) {
  .ts__bar {
    flex-direction: column;
    align-items: stretch;
  }

  .ts__selectors {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
    
    .ts__select, .ts__picker {
      width: 100% !important;
    }
  }

  .ts__actions {
    justify-content: flex-end;
    margin-top: 4px;
  }
}
</style>
