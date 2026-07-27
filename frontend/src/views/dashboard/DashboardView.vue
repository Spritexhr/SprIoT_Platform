<template>
  <div class="dashboard-view" v-loading="loading">
    <div class="iot-page-header">
      <div>
        <h1 class="iot-page-title">{{ ls.t('dashboard.title') }}</h1>
        <p class="iot-page-subtitle">{{ ls.t('dashboard.subtitle') }}</p>
      </div>
      <el-button :icon="Refresh" @click="fetchStats" :loading="loading">
        {{ ls.t('dashboard.refresh') }}
      </el-button>
    </div>

    <section class="ops-overview iot-mb-lg">
      <div class="ops-overview__main">
        <span class="ops-eyebrow">系统状态</span>
        <div class="ops-overview__headline">
          <strong>{{ onlineRate }}%</strong>
          <span>资源在线率</span>
        </div>
        <div class="ops-overview__meta">
          {{ onlineTotal }} / {{ totalResources }} 在线 · 24h 数据 {{ data24hTotal }} 条
        </div>
      </div>
      <div class="ops-status-strip">
        <button type="button" class="ops-status-item" @click="router.push('/sensors')">
          <span class="ops-status-item__icon is-sensor"><el-icon><Cpu /></el-icon></span>
          <span>传感器</span>
          <strong>{{ stats.sensor_online }}/{{ stats.sensor_total }}</strong>
        </button>
        <button type="button" class="ops-status-item" @click="router.push('/devices')">
          <span class="ops-status-item__icon is-device"><el-icon><Monitor /></el-icon></span>
          <span>设备</span>
          <strong>{{ stats.device_online }}/{{ stats.device_total }}</strong>
        </button>
        <button type="button" class="ops-status-item" @click="router.push('/automation')">
          <span class="ops-status-item__icon is-rule"><el-icon><SetUp /></el-icon></span>
          <span>自动化</span>
          <strong>{{ stats.rule_total }}</strong>
        </button>
        <div class="ops-status-item">
          <span class="ops-status-item__icon is-data"><el-icon><DataLine /></el-icon></span>
          <span>链路</span>
          <strong>{{ mqttLabel }} / {{ wsLabel }}</strong>
        </div>
      </div>
    </section>

    <section class="dashboard-layout iot-mb-lg">
      <div class="health-panel iot-card">
        <div class="panel-heading">
          <div>
            <span class="panel-kicker">运行状态</span>
            <h2>资源健康</h2>
          </div>
          <el-button text type="primary" size="small" @click="fetchStats">刷新</el-button>
        </div>
        <div class="health-grid">
          <button type="button" class="health-tile" @click="router.push('/sensors')">
            <div class="health-tile__top">
              <span>传感器</span>
              <strong>{{ sensorRate }}%</strong>
            </div>
            <div class="health-meter" role="progressbar" aria-label="传感器在线率" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="sensorRate"><span :style="{ width: `${sensorRate}%` }"></span></div>
            <p>{{ stats.sensor_online }} 在线 · {{ offlineSensors }} 离线</p>
          </button>
          <button type="button" class="health-tile" @click="router.push('/devices')">
            <div class="health-tile__top">
              <span>设备</span>
              <strong>{{ deviceRate }}%</strong>
            </div>
            <div class="health-meter" role="progressbar" aria-label="设备在线率" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="deviceRate"><span :style="{ width: `${deviceRate}%` }"></span></div>
            <p>{{ stats.device_online }} 在线 · {{ offlineDevices }} 离线</p>
          </button>
          <div class="health-tile health-tile--quiet">
            <div class="health-tile__top">
              <span>数据吞吐</span>
              <strong>{{ data24hTotal }}</strong>
            </div>
            <div class="health-split">
              <span>传感器 {{ stats.sensor_data_24h }}</span>
              <span>设备 {{ stats.device_data_24h }}</span>
            </div>
          </div>
          <div class="health-tile health-tile--quiet">
            <div class="health-tile__top">
              <span>连接状态</span>
              <strong :class="{ 'is-online-text': mqttStatus.is_connected }">{{ mqttLabel }}</strong>
            </div>
            <div class="health-split">
              <span>MQTT {{ mqttStatus.broker || '--' }}</span>
              <span>WS {{ wsLabel }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="activity-panel iot-card">
        <div class="panel-heading">
          <div>
            <span class="panel-kicker">实时动态</span>
            <h2>最近活动</h2>
          </div>
        </div>
        <div class="activity-list">
          <button
            v-for="item in recentActivity"
            :key="`${item.kind}-${item.id}`"
            type="button"
            class="activity-row"
            @click="router.push(item.href)"
          >
            <span class="iot-status-dot" :class="item.online ? 'iot-status-dot--online' : 'iot-status-dot--offline'"></span>
            <span class="activity-row__main">
              <strong>{{ item.name }}</strong>
              <small>{{ item.type }} · {{ item.time ? timeAgo(item.time) : '暂无数据' }}</small>
            </span>
            <span class="activity-row__value">{{ item.preview }}</span>
          </button>
          <el-empty v-if="!recentActivity.length" description="暂无最近活动" :image-size="68" />
        </div>
      </div>
    </section>

    <section class="dashboard-lists">
      <div class="compact-panel iot-card">
        <div class="panel-heading">
          <div>
            <span class="panel-kicker">传感器</span>
            <h2>{{ ls.t('dashboard.recentSensors') }}</h2>
          </div>
          <el-button text size="small" type="primary" @click="router.push('/sensors')">
            {{ ls.t('dashboard.viewAll') }}
          </el-button>
        </div>
        <div class="compact-list">
          <button v-for="row in recentSensors" :key="row.sensor_id" type="button" class="compact-row" @click="router.push(`/sensors/${row.sensor_id}`)">
            <span class="iot-status-dot" :class="row.is_online ? 'iot-status-dot--online' : 'iot-status-dot--offline'"></span>
            <span class="compact-row__name">{{ row.name }}</span>
            <span class="compact-row__data">
              <template v-if="row.latest_data">
                <span v-for="(val, key) in row.latest_data" :key="key">
                  {{ key }} {{ formatVal(val) }}
                </span>
              </template>
              <span v-else>--</span>
            </span>
            <span class="compact-row__time" :class="{ 'time-fresh': isFresh(row.latest_time) }">
              {{ row.latest_time ? timeAgo(row.latest_time) : '--' }}
            </span>
          </button>
          <el-empty v-if="!recentSensors.length" description="暂无传感器数据" :image-size="68" />
        </div>
      </div>

      <div class="compact-panel iot-card">
        <div class="panel-heading">
          <div>
            <span class="panel-kicker">设备</span>
            <h2>{{ ls.t('dashboard.deviceStatus') }}</h2>
          </div>
          <el-button text size="small" type="primary" @click="router.push('/devices')">
            {{ ls.t('dashboard.viewAll') }}
          </el-button>
        </div>
        <div class="compact-list">
          <button v-for="row in recentDevices" :key="row.device_id" type="button" class="compact-row" @click="router.push(`/devices/${row.device_id}`)">
            <span class="iot-status-dot" :class="row.is_online ? 'iot-status-dot--online' : 'iot-status-dot--offline'"></span>
            <span class="compact-row__name">{{ row.name }}</span>
            <span class="compact-row__data">
              <template v-if="row.latest_data">
                <span v-for="(val, key) in row.latest_data" :key="key">
                  {{ key }} {{ formatVal(val) }}
                </span>
              </template>
              <span v-else>--</span>
            </span>
            <span class="compact-row__time" :class="{ 'time-fresh': isFresh(row.latest_time) }">
              {{ row.latest_time ? timeAgo(row.latest_time) : '--' }}
            </span>
          </button>
          <el-empty v-if="!recentDevices.length" description="暂无设备状态" :image-size="68" />
        </div>
      </div>

      <div class="compact-panel iot-card">
        <div class="panel-heading">
          <div>
            <span class="panel-kicker">自动化</span>
            <h2>{{ ls.t('dashboard.automationRules') }}</h2>
          </div>
          <el-button text size="small" type="primary" @click="router.push('/automation')">
            {{ ls.t('dashboard.manageRules') }}
          </el-button>
        </div>
        <div class="compact-list">
          <button v-for="row in recentRules" :key="row.id" type="button" class="compact-row compact-row--rule" @click="router.push(`/automation/${row.id}`)">
            <span class="rule-dot"></span>
            <span class="compact-row__name">{{ row.name }}</span>
            <code class="script-id-tag">{{ row.script_id || '--' }}</code>
            <span class="compact-row__time">{{ formatTime(row.updated_at) }}</span>
          </button>
          <el-empty v-if="!recentRules.length" :description="ls.t('dashboard.noRules')" :image-size="68" />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Cpu, Monitor, SetUp, DataLine, Refresh } from '@element-plus/icons-vue'
import { getDashboardStats, getMqttStatus } from '@/api/system'
import { useLocaleStore } from '@/stores/locale'
import { useWebSocket, buildWsUrl } from '@/composables/useWebSocket'

const router = useRouter()
const ls = useLocaleStore()
const loading = ref(false)
const mqttStatus = reactive({
  broker: '',
  port: '',
  is_connected: false,
})

const stats = reactive({
  sensor_total: 0,
  sensor_online: 0,
  device_total: 0,
  device_online: 0,
  rule_total: 0,
  sensor_data_24h: 0,
  device_data_24h: 0,
  recent_sensors: [],
  recent_devices: [],
  recent_rules: [],
})

const totalResources = computed(() => stats.sensor_total + stats.device_total)
const onlineTotal = computed(() => stats.sensor_online + stats.device_online)
const data24hTotal = computed(() => stats.sensor_data_24h + stats.device_data_24h)
const onlineRate = computed(() => percent(onlineTotal.value, totalResources.value))
const sensorRate = computed(() => percent(stats.sensor_online, stats.sensor_total))
const deviceRate = computed(() => percent(stats.device_online, stats.device_total))
const offlineSensors = computed(() => Math.max(stats.sensor_total - stats.sensor_online, 0))
const offlineDevices = computed(() => Math.max(stats.device_total - stats.device_online, 0))
const recentSensors = computed(() => (stats.recent_sensors || []).slice(0, 8))
const recentDevices = computed(() => (stats.recent_devices || []).slice(0, 8))
const recentRules = computed(() => (stats.recent_rules || []).slice(0, 8))
const recentActivity = computed(() => {
  const sensors = (stats.recent_sensors || []).map((item) => ({
    kind: 'sensor',
    id: item.sensor_id,
    name: item.name,
    type: item.type_name || '传感器',
    online: item.is_online,
    time: item.latest_time,
    href: `/sensors/${item.sensor_id}`,
    preview: previewData(item.latest_data),
  }))
  const devices = (stats.recent_devices || []).map((item) => ({
    kind: 'device',
    id: item.device_id,
    name: item.name,
    type: item.type_name || '设备',
    online: item.is_online,
    time: item.latest_time,
    href: `/devices/${item.device_id}`,
    preview: previewData(item.latest_data),
  }))
  return [...sensors, ...devices]
    .sort((a, b) => new Date(b.time || 0) - new Date(a.time || 0))
    .slice(0, 7)
})
const mqttLabel = computed(() => mqttStatus.is_connected ? 'MQTT在线' : 'MQTT离线')
const wsLabel = computed(() => {
  const states = [sensorSocket.displayStatus.value, deviceSocket.displayStatus.value]
  if (states.includes('open')) return 'WS在线'
  if (states.includes('unauthorized')) return 'WS未授权'
  if (states.includes('connecting')) return 'WS连接中'
  return 'WS离线'
})

function percent(value, total) {
  if (!total) return 0
  return Math.round((value / total) * 100)
}

async function fetchStats() {
  loading.value = true
  try {
    const [data, mqtt] = await Promise.all([
      getDashboardStats(),
      getMqttStatus().catch(() => null),
    ])
    Object.assign(stats, data)
    if (mqtt) Object.assign(mqttStatus, mqtt)
  } catch {
    ElMessage.error(ls.t('dashboard.fetchError'))
  } finally {
    loading.value = false
  }
}

function formatVal(val) {
  if (val === null || val === undefined) return '--'
  if (typeof val === 'boolean') return val ? ls.t('dashboard.on') : ls.t('dashboard.off')
  if (typeof val === 'number') return Number(val.toFixed(2))
  return String(val)
}

function previewData(data) {
  if (!data || typeof data !== 'object') return '--'
  const [key, val] = Object.entries(data)[0] || []
  if (!key) return '--'
  return `${key} ${formatVal(val)}`
}

function timeAgo(dateStr) {
  if (!dateStr) return '--'
  const now = new Date()
  const past = new Date(dateStr)
  const diff = Math.floor((now - past) / 1000)
  if (diff < 5) return ls.t('dashboard.justNow')
  if (diff < 60) return `${diff}${ls.t('dashboard.secondsAgo')}`
  if (diff < 3600) return `${Math.floor(diff / 60)}${ls.t('dashboard.minutesAgo')}`
  if (diff < 86400) return `${Math.floor(diff / 3600)}${ls.t('dashboard.hoursAgo')}`
  return `${Math.floor(diff / 86400)}${ls.t('dashboard.daysAgo')}`
}

function isFresh(dateStr) {
  if (!dateStr) return false
  return (new Date() - new Date(dateStr)) < 300000
}

// ==================== 实时推送 ====================
// 订阅传感器/设备两条 channel，patch 对应表格
function onSensorData(data) {
  if (!data || !data.sensor_id) return
  const row = stats.recent_sensors?.find(r => r.sensor_id === data.sensor_id)
  if (!row) return
  row.latest_data = data.data
  if (data.timestamp) row.latest_time = new Date(data.timestamp * 1000).toISOString()
  row.is_online = true
}

function onSensorStatus(data) {
  if (!data || !data.sensor_id) return
  const row = stats.recent_sensors?.find(r => r.sensor_id === data.sensor_id)
  if (!row) return
  const before = row.is_online
  row.is_online = !!data.is_online
  if (data.last_seen) row.latest_time = new Date(data.last_seen * 1000).toISOString()
  if (before !== row.is_online) {
    stats.sensor_online += row.is_online ? 1 : -1
  }
}

function onDeviceStatus(data) {
  if (!data || !data.device_id) return
  const row = stats.recent_devices?.find(r => r.device_id === data.device_id)
  if (!row) return
  const before = row.is_online
  row.latest_data = data.status
  if (data.timestamp) row.latest_time = new Date(data.timestamp * 1000).toISOString()
  row.is_online = !!data.is_online
  if (data.last_seen) row.latest_time = new Date(data.last_seen * 1000).toISOString()
  if (before !== row.is_online) {
    stats.device_online += row.is_online ? 1 : -1
  }
}

const sensorSocket = useWebSocket(
  () => buildWsUrl('/ws/sensors/'),
  {
    'sensor.data': onSensorData,
    'sensor.status': onSensorStatus,
  },
)

const deviceSocket = useWebSocket(
  () => buildWsUrl('/ws/devices/'),
  { 'device.status': onDeviceStatus },
)

function formatTime(str) {
  if (!str) return '--'
  const d = new Date(str)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => { fetchStats() })
</script>

<style scoped>
.ops-overview {
  display: grid;
  grid-template-columns: minmax(280px, 0.76fr) minmax(0, 1.64fr);
  gap: var(--iot-spacing-md);
  align-items: stretch;
}

.ops-overview__main,
.ops-status-strip,
.health-panel,
.activity-panel,
.compact-panel {
  border: 1px solid var(--iot-border-color-light);
}

.ops-overview__main {
  min-height: 166px;
  padding: 24px 26px;
  border-radius: var(--iot-radius-lg);
  background: var(--iot-bg-card);
  box-shadow: var(--iot-shadow-sm);
}

.ops-eyebrow,
.panel-kicker {
  color: var(--iot-color-primary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.ops-overview__headline {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-top: 22px;
}

.ops-overview__headline strong {
  color: var(--iot-text-primary);
  font-size: 54px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.ops-overview__headline span {
  color: var(--iot-text-regular);
  font-size: 16px;
  font-weight: 600;
}

.ops-overview__meta {
  margin-top: 18px;
  color: var(--iot-text-secondary);
  font-size: 13px;
}

.ops-status-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  padding: 16px;
  border-radius: var(--iot-radius-lg);
  background: color-mix(in srgb, var(--iot-bg-card) 86%, var(--iot-color-primary-bg));
  box-shadow: var(--iot-shadow-sm);
}

.ops-status-item {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  grid-template-rows: auto auto;
  align-content: center;
  align-items: stretch;
  column-gap: 12px;
  row-gap: 3px;
  min-width: 0;
  min-height: 128px;
  padding: 18px 16px;
  border: 1px solid color-mix(in srgb, var(--iot-border-color-light) 85%, transparent);
  border-radius: 8px;
  background: var(--iot-bg-card);
  color: inherit;
  text-align: left;
}

button.ops-status-item {
  cursor: pointer;
  transition: border-color .18s, transform .18s, box-shadow .18s;
}

button.ops-status-item:hover {
  border-color: color-mix(in srgb, var(--iot-color-primary) 46%, transparent);
  box-shadow: var(--iot-shadow-sm);
  transform: translateY(-1px);
}

.ops-status-item__icon {
  display: grid;
  grid-row: 1 / 3;
  width: 32px;
  height: 32px;
  align-self: center;
  place-items: center;
  border-radius: 8px;
  color: #fff;
}

.ops-status-item__icon.is-sensor { background: var(--iot-color-primary); }
.ops-status-item__icon.is-device { background: var(--iot-color-success); }
.ops-status-item__icon.is-rule { background: var(--iot-color-warning); }
.ops-status-item__icon.is-data { background: #8b7b6b; }

.ops-status-item span:not(.ops-status-item__icon) {
  align-self: end;
  color: var(--iot-text-secondary);
  font-size: 12px;
  line-height: 1.2;
}

.ops-status-item strong {
  align-self: start;
  min-width: 0;
  overflow: hidden;
  color: var(--iot-text-primary);
  font-size: 19px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.dashboard-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(320px, .9fr);
  gap: var(--iot-spacing-md);
}

.health-panel,
.activity-panel,
.compact-panel {
  padding: 20px;
  border-radius: var(--iot-radius-lg);
  background: var(--iot-bg-card);
  box-shadow: var(--iot-shadow-sm);
}

.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
  margin-bottom: 16px;
}

.panel-heading h2 {
  margin: 4px 0 0;
  color: var(--iot-text-primary);
  font-size: 17px;
}

.health-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  align-items: stretch;
}

.health-tile {
  min-width: 0;
  min-height: 132px;
  padding: 18px;
  border: 1px solid var(--iot-border-color-light);
  border-radius: 8px;
  background: color-mix(in srgb, var(--iot-bg-card) 92%, var(--iot-bg-page));
  color: inherit;
  text-align: left;
}

button.health-tile {
  cursor: pointer;
  transition: border-color .18s, transform .18s;
}

button.health-tile:hover {
  border-color: color-mix(in srgb, var(--iot-color-primary) 48%, transparent);
  transform: translateY(-1px);
}

.health-tile__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.health-tile__top span {
  color: var(--iot-text-secondary);
  font-size: 13px;
}

.health-tile__top strong {
  color: var(--iot-text-primary);
  font-size: 26px;
  font-variant-numeric: tabular-nums;
}

.is-online-text {
  color: var(--iot-color-success) !important;
}

.health-meter {
  height: 8px;
  margin: 20px 0 14px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--iot-border-color-lighter);
}

.health-meter span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--iot-color-primary);
}

.health-tile p,
.health-split {
  margin: 0;
  color: var(--iot-text-secondary);
  font-size: 12px;
}

.health-split {
  display: grid;
  gap: 8px;
  margin-top: 18px;
}

.activity-list,
.compact-list {
  display: grid;
  gap: 7px;
}

.activity-row,
.compact-row {
  display: grid;
  align-items: center;
  width: 100%;
  min-width: 0;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background .18s, border-color .18s;
}

.activity-row {
  grid-template-columns: 12px minmax(0, 1fr) minmax(82px, auto);
  gap: 10px;
  min-height: 48px;
  padding: 9px 10px;
}

.activity-row:hover,
.compact-row:hover {
  border-color: var(--iot-border-color-light);
  background: var(--iot-bg-card-hover);
}

.activity-row__main {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.activity-row__main strong,
.compact-row__name {
  overflow: hidden;
  color: var(--iot-text-primary);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-row__main small,
.activity-row__value,
.compact-row__data,
.compact-row__time {
  color: var(--iot-text-secondary);
  font-size: 12px;
}

.activity-row__value {
  overflow: hidden;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dashboard-lists {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--iot-spacing-md);
}

.compact-row {
  grid-template-columns: 12px minmax(96px, 1fr) minmax(90px, 1.2fr) 72px;
  gap: 9px;
  min-height: 42px;
  padding: 9px 10px;
}

.compact-row--rule {
  grid-template-columns: 12px minmax(96px, 1fr) minmax(80px, auto) 112px;
}

.compact-row__data {
  display: inline-flex;
  min-width: 0;
  gap: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-row__time {
  text-align: right;
  white-space: nowrap;
}

.time-fresh {
  color: var(--iot-color-success);
  font-weight: 600;
}

.script-id-tag {
  overflow: hidden;
  padding: 2px 6px;
  border: 1px solid var(--iot-border-color-light);
  border-radius: 4px;
  background: var(--iot-bg-page);
  color: var(--iot-color-primary);
  font-family: var(--iot-font-mono, 'Courier New', monospace);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rule-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--iot-color-warning);
}

@media (max-width: 1180px) {
  .ops-overview,
  .dashboard-layout,
  .dashboard-lists {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 860px) {
  .ops-status-strip,
  .health-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .iot-page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--iot-spacing-sm);
  }

  .iot-page-subtitle {
    display: none;
  }

  .ops-overview__headline strong {
    font-size: 42px;
  }

  .compact-row,
  .compact-row--rule {
    grid-template-columns: 12px minmax(0, 1fr) auto;
  }

  .compact-row__data,
  .script-id-tag {
    grid-column: 2 / 4;
  }

  .compact-row__time {
    grid-column: 3;
    grid-row: 1;
  }
}

@media (max-width: 520px) {
  .ops-status-strip,
  .health-grid {
    grid-template-columns: 1fr;
  }

  .activity-row {
    grid-template-columns: 12px minmax(0, 1fr);
  }

  .activity-row__value {
    grid-column: 2;
    text-align: left;
  }
}

/* Apple Design refinement：大层级负责理解，小反馈保持即时。 */
.ops-overview {
  grid-template-columns: minmax(300px, 0.82fr) minmax(0, 1.58fr);
  gap: 18px;
}

.ops-overview__main,
.ops-status-strip,
.health-panel,
.activity-panel,
.compact-panel {
  border-color: var(--iot-border-color-light);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-sm);
  backdrop-filter: blur(var(--iot-material-blur)) saturate(165%);
  -webkit-backdrop-filter: blur(var(--iot-material-blur)) saturate(165%);
}

.ops-overview__main {
  position: relative;
  min-height: 190px;
  overflow: hidden;
  padding: 28px 30px;
  border-radius: var(--iot-radius-xl);
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--iot-bg-card) 86%, var(--iot-color-primary-bg)), var(--iot-bg-card)),
    var(--iot-bg-card);
}

.ops-overview__main::after {
  position: absolute;
  top: -75px;
  right: -50px;
  width: 210px;
  height: 210px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--iot-color-primary-soft), transparent 68%);
  pointer-events: none;
  content: '';
}

.ops-eyebrow,
.panel-kicker {
  color: var(--iot-color-primary);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.075em;
}

.ops-eyebrow {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  padding: 3px 9px;
  border: 1px solid color-mix(in srgb, var(--iot-color-primary) 18%, transparent);
  border-radius: var(--iot-radius-pill);
  background: var(--iot-color-primary-bg);
}

.ops-overview__headline {
  position: relative;
  z-index: 1;
  gap: 13px;
  margin-top: 20px;
}

.ops-overview__headline strong {
  font-family: var(--iot-font-display);
  font-size: clamp(48px, 5vw, 66px);
  font-weight: 660;
  letter-spacing: -0.055em;
}

.ops-overview__headline span {
  font-size: 15px;
  font-weight: 580;
}

.ops-overview__meta {
  position: relative;
  z-index: 1;
  margin-top: 15px;
  font-size: var(--iot-font-size-sm);
}

.ops-status-strip {
  gap: 9px;
  padding: 10px;
  border-radius: var(--iot-radius-xl);
  background: var(--iot-material-regular);
}

.ops-status-item {
  min-height: 168px;
  padding: 21px 16px;
  border-color: transparent;
  border-radius: 15px;
  background: color-mix(in srgb, var(--iot-bg-card-solid) 70%, transparent);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
}

button.ops-status-item {
  transition: transform var(--iot-transition-instant), border-color var(--iot-transition-fast), background-color var(--iot-transition-fast), box-shadow var(--iot-transition-fast);
}

button.ops-status-item:hover {
  border-color: color-mix(in srgb, var(--iot-color-primary) 20%, var(--iot-border-color-light));
  background: var(--iot-bg-card-hover);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-sm);
  transform: translateY(-1px);
}

button.ops-status-item:active {
  transform: scale(0.975);
  transition-duration: 100ms;
}

.ops-status-item__icon {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 5px 12px rgba(33, 30, 27, 0.1);
}

.ops-status-item__icon.is-data { background: var(--iot-text-secondary); }

.ops-status-item strong {
  font-family: var(--iot-font-display);
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.02em;
}

.dashboard-layout,
.dashboard-lists {
  gap: 18px;
}

.health-panel,
.activity-panel,
.compact-panel {
  padding: 22px;
  border-radius: var(--iot-radius-xl);
  background: var(--iot-bg-card);
}

.panel-heading {
  margin-bottom: 18px;
}

.panel-heading h2 {
  margin-top: 5px;
  font-family: var(--iot-font-display);
  font-size: 18px;
  font-weight: 650;
  letter-spacing: -0.018em;
}

.health-grid { gap: 10px; }

.health-tile {
  min-height: 138px;
  padding: 18px;
  border-color: var(--iot-border-color-lighter);
  border-radius: 15px;
  background: color-mix(in srgb, var(--iot-bg-card-solid) 64%, transparent);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
}

button.health-tile {
  transition: transform var(--iot-transition-instant), border-color var(--iot-transition-fast), background-color var(--iot-transition-fast);
}

button.health-tile:hover {
  border-color: color-mix(in srgb, var(--iot-color-primary) 24%, var(--iot-border-color-light));
  background: var(--iot-bg-card-hover);
  transform: translateY(-1px);
}

button.health-tile:active {
  transform: scale(0.985);
  transition-duration: 100ms;
}

.health-tile__top strong {
  font-family: var(--iot-font-display);
  font-size: 28px;
  font-weight: 650;
  letter-spacing: -0.035em;
}

.health-meter {
  height: 6px;
  margin: 22px 0 15px;
  background: color-mix(in srgb, var(--iot-text-secondary) 10%, transparent);
}

.health-meter span {
  background: linear-gradient(90deg, var(--iot-color-primary-dark), var(--iot-color-primary-light));
  transition: width var(--iot-transition-slow);
}

.activity-list,
.compact-list { gap: 2px; }

.activity-row,
.compact-row {
  border: 0;
  border-radius: 11px;
  box-shadow: inset 0 -1px 0 var(--iot-separator);
  transition: background-color var(--iot-transition-fast), transform var(--iot-transition-instant);
}

.activity-row:last-child,
.compact-row:last-child { box-shadow: none; }

.activity-row:hover,
.compact-row:hover {
  border-color: transparent;
  background: var(--iot-material-thin);
}

.activity-row:active,
.compact-row:active {
  transform: scale(0.992);
  transition-duration: 100ms;
}

.activity-row { min-height: 52px; }
.compact-row { min-height: 46px; }

.activity-row__main strong,
.compact-row__name { font-weight: 600; }

.script-id-tag {
  padding: 3px 7px;
  border-radius: var(--iot-radius-sm);
  background: var(--iot-material-thin);
}

@media (max-width: 1180px) {
  .ops-overview,
  .dashboard-layout,
  .dashboard-lists { grid-template-columns: 1fr; }

  .ops-status-item { min-height: 120px; }
}

@media (max-width: 768px) {
  .iot-page-subtitle { display: block; }
  .ops-overview__main { min-height: 174px; padding: 24px; }
  .ops-overview__headline strong { font-size: 48px; }
  .health-panel,
  .activity-panel,
  .compact-panel { padding: 18px; }
}

@media (max-width: 520px) {
  .ops-overview { gap: 12px; }
  .ops-status-strip { grid-template-columns: 1fr 1fr; }
  .ops-status-item { min-height: 106px; padding: 15px 12px; }
  .ops-status-item:last-child strong { font-size: 15px; }
  .health-grid { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  button.ops-status-item:hover,
  button.ops-status-item:active,
  button.health-tile:hover,
  button.health-tile:active,
  .activity-row:active,
  .compact-row:active { transform: none; }

  .health-meter span { transition: none; }
}

@media (prefers-reduced-transparency: reduce) {
  .ops-overview__main,
  .ops-status-strip,
  .health-panel,
  .activity-panel,
  .compact-panel {
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
}
</style>
