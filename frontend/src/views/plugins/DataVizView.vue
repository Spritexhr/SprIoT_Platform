<template>
  <div class="data-viz-view">
    <div class="iot-page-header">
      <div>
        <h1 class="iot-page-title">数据可视化</h1>
        <p class="iot-page-subtitle">按时间段查看传感器/设备的数值序列与状态事件</p>
      </div>
      <div class="header-actions">
        <el-button :icon="ArrowLeft" @click="router.push('/plugins')">返回插件中心</el-button>
        <el-button type="primary" :icon="Refresh" :loading="loading" @click="fetchSeries">
          刷新
        </el-button>
      </div>
    </div>

    <!-- 控制栏 -->
    <div class="iot-card iot-mb-lg">
      <div class="filter-bar">
        <el-select
          v-model="selectedSource"
          placeholder="选择数据源"
          style="width: 280px"
          :loading="sourcesLoading"
          @change="onSourceChange"
        >
          <el-option-group label="传感器">
            <el-option
              v-for="s in sources.sensors"
              :key="`sensor:${s.id}`"
              :label="`${s.name} (${s.id})`"
              :value="`sensor:${s.id}`"
            >
              <span style="float: left">{{ s.name }}</span>
              <span class="option-meta">{{ s.id }}</span>
            </el-option>
          </el-option-group>
          <el-option-group label="设备">
            <el-option
              v-for="d in sources.devices"
              :key="`device:${d.id}`"
              :label="`${d.name} (${d.id})`"
              :value="`device:${d.id}`"
            >
              <span style="float: left">{{ d.name }}</span>
              <span class="option-meta">{{ d.id }}</span>
            </el-option>
          </el-option-group>
        </el-select>

        <el-select
          v-model="selectedFields"
          multiple
          collapse-tags
          collapse-tags-tooltip
          placeholder="选择字段"
          style="width: 240px"
          :disabled="!availableFields.length"
        >
          <el-option v-for="f in availableFields" :key="f" :label="f" :value="f" />
        </el-select>

        <el-select v-model="quickRange" style="width: 120px" @change="onQuickRangeChange">
          <el-option label="近 1 小时" :value="1" />
          <el-option label="近 6 小时" :value="6" />
          <el-option label="近 24 小时" :value="24" />
          <el-option label="近 7 天" :value="168" />
          <el-option label="自定义" value="custom" />
        </el-select>

        <el-date-picker
          v-if="quickRange === 'custom'"
          v-model="customRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          format="YYYY-MM-DD HH:mm"
          value-format="YYYY-MM-DDTHH:mm:ss"
          style="width: 380px"
        />
      </div>
    </div>

    <!-- 图表 -->
    <div v-loading="loading" class="iot-card chart-card">
      <div v-if="!selectedSource" class="chart-empty">
        <el-empty description="请先选择一个数据源" />
      </div>
      <div v-else-if="!hasPoints" class="chart-empty">
        <el-empty :description="loading ? '加载中…' : '当前时间窗内无数据'" />
      </div>
      <div v-else>
        <div class="chart-meta">
          <el-tag size="small">{{ seriesData.kind === 'sensor' ? '传感器' : '设备' }}</el-tag>
          <span class="meta-name">{{ seriesData.name }}</span>
          <span class="meta-text">{{ seriesData.type }}</span>
          <el-divider direction="vertical" />
          <span class="meta-text">{{ seriesData.count }} 条数据点</span>
          <el-tag v-if="seriesData.truncated" type="warning" size="small">
            已截断至最近 {{ seriesData.points.length }} 条
          </el-tag>
        </div>
        <div ref="chartEl" class="chart-canvas" />
      </div>
    </div>

    <!-- 状态事件（heartbeat 不展示） -->
    <div v-if="displayEvents.length" class="iot-card iot-mb-lg events-card">
      <div class="iot-card__header">
        <span class="section-title">状态事件 ({{ displayEvents.length }})</span>
      </div>
      <div class="iot-card__body">
        <el-table :data="displayEvents" stripe size="small" max-height="320">
          <el-table-column label="时间" width="200">
            <template #default="{ row }">{{ formatTime(row.t) }}</template>
          </el-table-column>
          <el-table-column prop="event" label="事件" width="180" />
          <el-table-column label="详细数据">
            <template #default="{ row }">
              <code class="event-data">{{ JSON.stringify(row.data) }}</code>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, ArrowLeft } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { getDataVizSources, getDataVizSeries } from '@/api/plugins'
import { readChartTheme, withAlpha } from '@/utils/chartTheme'

const router = useRouter()

// ==================== 数据状态 ====================
const sources = ref({ sensors: [], devices: [] })
const sourcesLoading = ref(false)
const selectedSource = ref('') // 形如 "sensor:DHT11-001"
const selectedFields = ref([])
const quickRange = ref(24)
const customRange = ref([])
const seriesData = ref({ points: [], events: [], count: 0, truncated: false })
const loading = ref(false)

// ==================== Chart 实例 ====================
const chartEl = ref(null)
let chartInstance = null

const hasPoints = computed(() => (seriesData.value.points || []).length > 0)
const displayEvents = computed(() =>
  (seriesData.value.events || []).filter((e) => e.event !== 'heartbeat'),
)

// 当前来源对象（拆分 selectedSource）
const currentSource = computed(() => {
  if (!selectedSource.value) return null
  const [kind, id] = selectedSource.value.split(':')
  if (kind === 'sensor') {
    return sources.value.sensors.find((s) => s.id === id)
  }
  return sources.value.devices.find((d) => d.id === id)
})

const availableFields = computed(() => {
  const src = currentSource.value
  if (!src) return []
  return src.data_fields || src.config_parameters || []
})

// ==================== 事件 ====================
function onSourceChange() {
  // 切换源后默认选中前 2 个字段
  selectedFields.value = availableFields.value.slice(0, 2)
  fetchSeries()
}

function onQuickRangeChange() {
  if (quickRange.value !== 'custom') fetchSeries()
}

function getRange() {
  if (quickRange.value === 'custom') {
    if (!customRange.value || customRange.value.length !== 2) return null
    return { start: customRange.value[0], end: customRange.value[1] }
  }
  const end = new Date()
  const start = new Date(end.getTime() - quickRange.value * 3600 * 1000)
  return { start: start.toISOString(), end: end.toISOString() }
}

function formatTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
function pad(n) { return String(n).padStart(2, '0') }

// ==================== API ====================
async function fetchSources() {
  sourcesLoading.value = true
  try {
    const res = await getDataVizSources()
    sources.value = res
  } catch (e) {
    ElMessage.error('获取数据源失败')
    console.error(e)
  } finally {
    sourcesLoading.value = false
  }
}

async function fetchSeries() {
  if (!selectedSource.value) return
  const range = getRange()
  if (!range) {
    ElMessage.warning('请选择有效的时间范围')
    return
  }
  const [kind, sourceId] = selectedSource.value.split(':')
  loading.value = true
  try {
    const res = await getDataVizSeries({
      kind,
      sourceId,
      start: range.start,
      end: range.end,
    })
    seriesData.value = res
    await nextTick()
    renderChart()
  } catch (e) {
    ElMessage.error('获取数据失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ==================== 图表渲染 ====================
function coerceNumber(v) {
  if (v === null || v === undefined) return null
  if (typeof v === 'boolean') return v ? 1 : 0
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function renderChart() {
  if (!chartEl.value) return
  if (!chartInstance) {
    chartInstance = echarts.init(chartEl.value)
  }
  const theme = readChartTheme()
  const points = seriesData.value.points || []
  const events = seriesData.value.events || []
  const fields = selectedFields.value.length
    ? selectedFields.value
    : availableFields.value.slice(0, 2)

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

  // 状态事件标记到图表中（mark line），heartbeat 纯心跳包不展示
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
            fontSize: 10,
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
      color: theme.textRegular,
      fontFamily: theme.fontFamily,
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
    grid: { left: 50, right: 30, top: 48, bottom: 72, containLabel: true },
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
        height: 24,
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

function handleResize() {
  chartInstance?.resize()
}

// ==================== 生命周期 ====================
let themeObserver = null
let themeFrame = null

onMounted(() => {
  window.addEventListener('resize', handleResize)

  themeObserver = new MutationObserver(() => {
    if (themeFrame) cancelAnimationFrame(themeFrame)
    themeFrame = requestAnimationFrame(() => {
      themeFrame = null
      if (chartInstance && hasPoints.value) renderChart()
    })
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })

  fetchSources()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  themeObserver?.disconnect()
  themeObserver = null
  if (themeFrame) {
    cancelAnimationFrame(themeFrame)
    themeFrame = null
  }
  chartInstance?.dispose()
  chartInstance = null
})

watch(selectedFields, () => {
  if (selectedSource.value) renderChart()
})
</script>

<style scoped>
.data-viz-view {
  padding: 0;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 16px;
  align-items: center;
}

.option-meta {
  float: right;
  font-size: 12px;
  color: var(--iot-text-secondary);
  font-family: var(--iot-font-mono, monospace);
}

.chart-card {
  padding: 20px;
}

.chart-empty {
  padding: 60px 0;
}

.chart-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.meta-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--iot-text-primary);
}

.meta-text {
  font-size: 12px;
  color: var(--iot-text-secondary);
}

.chart-canvas {
  width: 100%;
  height: 460px;
}

.events-card {
  margin-top: 16px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
}

.event-data {
  font-family: var(--iot-font-mono, monospace);
  font-size: 12px;
  color: var(--iot-text-secondary);
  word-break: break-all;
}
</style>
