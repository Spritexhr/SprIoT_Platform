<template>
  <div class="settings-view">
    <div class="iot-page-header">
      <div>
        <h1 class="iot-page-title">{{ ls.t('settings.title') }}</h1>
        <p class="iot-page-subtitle">{{ ls.t('settings.subtitle') }}</p>
      </div>
    </div>

    <!-- ==================== 首次部署引导 ==================== -->
    <el-alert
      v-if="showFirstTimeBanner"
      class="iot-mb-lg"
      :title="ls.t('settings.firstTimeBannerTitle')"
      :description="ls.t('settings.firstTimeBanner')"
      type="warning"
      show-icon
      :closable="false"
    />

    <!-- ==================== MQTT 连接状态 ==================== -->
    <div class="iot-card iot-mb-lg">
      <div class="iot-card__header">
        <span class="section-title">{{ ls.t('settings.mqttStatus') }}</span>
        <el-button text :icon="Refresh" :loading="mqttLoading" @click="fetchMqttStatus">
          {{ ls.t('common.refresh') }}
        </el-button>
      </div>
      <div class="iot-card__body">
        <div class="setting-item">
          <span class="setting-label">{{ ls.t('settings.brokerAddr') }}</span>
          <span class="setting-value">{{ mqttInfo.broker || '--' }}:{{ mqttInfo.port || '--' }}</span>
        </div>
        <div class="setting-item">
          <span class="setting-label">{{ ls.t('settings.connStatus') }}</span>
          <span
            class="iot-status-tag"
            :class="mqttInfo.is_connected ? 'iot-status-tag--online' : 'iot-status-tag--offline'"
          >
            <span
              class="iot-status-dot"
              :class="mqttInfo.is_connected ? 'iot-status-dot--online' : 'iot-status-dot--offline'"
            ></span>
            {{ mqttInfo.is_connected ? ls.t('settings.connected') : ls.t('settings.disconnected') }}
          </span>
        </div>
      </div>
    </div>

    <!-- ==================== 分组配置表单 ==================== -->
    <div
      v-for="group in groupedSchema"
      :key="group.category"
      class="iot-card iot-mb-lg"
      v-loading="schemaLoading"
    >
      <div class="iot-card__header">
        <span class="section-title">{{ groupLabel(group.category) }}</span>
        <div class="header-actions">
          <el-button
            v-if="isSuperuser && group.category === 'mqtt'"
            size="small"
            :icon="Connection"
            :loading="testingMqtt"
            @click="handleTestMqtt(group)"
          >
            {{ testingMqtt ? ls.t('settings.testMqttRunning') : ls.t('settings.testMqtt') }}
          </el-button>
          <el-button
            v-if="isSuperuser"
            type="primary"
            size="small"
            :loading="group._saving"
            :disabled="!groupDirty(group)"
            @click="handleSaveGroup(group)"
          >
            {{ ls.t('settings.saveSection') }}
          </el-button>
        </div>
      </div>
      <div class="iot-card__body">
        <el-form label-width="160px" label-position="left" @submit.prevent>
          <el-form-item
            v-for="field in group.fields"
            :key="field.key"
            :label="fieldLabel(field)"
            :error="field._error"
          >
            <!-- 数字 -->
            <el-input-number
              v-if="field.type === 'integer' || field.type === 'number'"
              v-model="field._value"
              :min="field.key.endsWith('_port') ? 1 : 0"
              :max="field.key.endsWith('_port') ? 65535 : undefined"
              :step="field.type === 'integer' ? 1 : 0.1"
              :precision="field.type === 'integer' ? 0 : undefined"
              :disabled="!isSuperuser"
              controls-position="right"
              style="width: 220px"
              @change="markDirty(field)"
            />
            <!-- 布尔 -->
            <el-switch
              v-else-if="field.type === 'boolean'"
              v-model="field._value"
              :disabled="!isSuperuser"
              @change="markDirty(field)"
            />
            <!-- 密码 -->
            <div v-else-if="field.secret" class="secret-row">
              <el-input
                v-if="field._editingSecret"
                v-model="field._value"
                type="password"
                show-password
                :disabled="!isSuperuser"
                style="max-width: 360px"
                @input="markDirty(field)"
              />
              <span v-else class="secret-placeholder">
                {{ (field._original || field._value) ? ls.t('settings.secretPlaceholder') : '—' }}
              </span>
              <el-button
                v-if="isSuperuser"
                size="small"
                text
                @click="toggleSecret(field)"
              >
                {{ field._editingSecret ? ls.t('settings.hideSecret') : ls.t('settings.revealSecret') }}
              </el-button>
            </div>
            <!-- 字符串（默认） -->
            <el-input
              v-else
              v-model="field._value"
              :disabled="!isSuperuser"
              style="max-width: 360px"
              @input="markDirty(field)"
            />
            <div v-if="field.description" class="field-desc">{{ field.description }}</div>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- ==================== 可用命令 ==================== -->
    <div v-if="isSuperuser" class="iot-card iot-mb-lg">
      <div class="iot-card__header">
        <span class="section-title">{{ ls.t('settings.commands') }}</span>
        <el-button
          :icon="Refresh"
          size="small"
          :loading="reloadLoading"
          @click="handleReloadConfig"
        >
          {{ ls.t('settings.applyConfig') }}
        </el-button>
      </div>
      <div class="iot-card__body">
        <div class="command-item">
          <div class="command-item__info">
            <span class="command-item__name">{{ ls.t('settings.cleanupData') }}</span>
            <span class="command-item__desc">{{ ls.t('settings.cleanupDesc') }}</span>
          </div>
          <el-button
            type="primary"
            :icon="VideoPlay"
            :loading="cleanupLoading"
            @click="handleCleanupOldData"
          >
            {{ ls.t('settings.execute') }}
          </el-button>
        </div>
      </div>
    </div>

    <!-- ==================== 高级 / 自定义配置（折叠） ==================== -->
    <div class="iot-card iot-mb-lg">
      <el-collapse v-model="advancedOpen">
        <el-collapse-item name="advanced">
          <template #title>
            <span class="section-title">{{ ls.t('settings.advancedConfig') }}</span>
            <span class="advanced-count" v-if="customConfigs.length">
              （{{ customConfigs.length }}）
            </span>
          </template>
          <div class="advanced-body">
            <p class="advanced-hint">{{ ls.t('settings.advancedHint') }}</p>
            <div class="advanced-actions" v-if="isSuperuser">
              <el-button size="small" type="primary" :icon="Plus" @click="openCustomDialog(null)">
                {{ ls.t('settings.addConfig') }}
              </el-button>
            </div>
            <el-table :data="customConfigs" v-loading="configsLoading" stripe size="small">
              <el-table-column prop="key" :label="ls.t('settings.configKey')" width="220" />
              <el-table-column prop="category" :label="ls.t('settings.category')" width="120" />
              <el-table-column :label="ls.t('settings.configValue')" min-width="200">
                <template #default="{ row }">
                  <span class="config-value">{{ formatValue(row.value) }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="description" :label="ls.t('settings.configDesc')" min-width="120" show-overflow-tooltip />
              <el-table-column v-if="isSuperuser" :label="ls.t('common.edit')" width="160" align="center" fixed="right">
                <template #default="{ row }">
                  <div class="config-actions">
                    <el-button type="primary" size="small" @click="openCustomDialog(row)">
                      {{ ls.t('common.edit') }}
                    </el-button>
                    <el-popconfirm
                      :title="ls.t('settings.deleteConfigConfirm')"
                      :confirm-button-text="ls.t('common.delete')"
                      :cancel-button-text="ls.t('common.cancel')"
                      @confirm="handleDeleteCustom(row.key)"
                    >
                      <template #reference>
                        <el-button plain type="danger" size="small">{{ ls.t('common.delete') }}</el-button>
                      </template>
                    </el-popconfirm>
                  </div>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="!configsLoading && customConfigs.length === 0" class="empty-hint">
              <el-empty :description="ls.t('settings.noConfig')" :image-size="80" />
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>

    <!-- 自定义配置弹窗（沿用旧 JSON 编辑器） -->
    <el-dialog
      v-model="customDialogVisible"
      :title="customForm.id ? ls.t('settings.editConfigTitle') : ls.t('settings.addConfigTitle')"
      width="600px"
      destroy-on-close
    >
      <el-form :model="customForm" label-width="100px" :rules="customRules" ref="customFormRef">
        <el-form-item :label="ls.t('settings.configKey')" prop="key">
          <el-input
            v-model="customForm.key"
            :placeholder="ls.t('settings.configKeyPlaceholder')"
            :disabled="!!customForm.id"
          />
        </el-form-item>
        <el-form-item :label="ls.t('settings.category')" prop="category">
          <el-select v-model="customForm.category" :placeholder="ls.t('settings.selectCategory')" filterable allow-create style="width: 100%">
            <el-option label="general" value="general" />
            <el-option label="mqtt" value="mqtt" />
            <el-option label="devices" value="devices" />
            <el-option label="data_retention" value="data_retention" />
          </el-select>
        </el-form-item>
        <el-form-item :label="ls.t('settings.configValue')" prop="value">
          <el-input
            v-model="customForm.valueJson"
            type="textarea"
            :rows="4"
            :placeholder="ls.t('settings.configValuePlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="ls.t('settings.configDesc')">
          <el-input v-model="customForm.description" :placeholder="ls.t('common.optional')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="customDialogVisible = false">{{ ls.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="customSaving" @click="handleSaveCustom">{{ ls.t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, reactive, h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Plus, VideoPlay, Connection } from '@element-plus/icons-vue'
import { getMqttStatus } from '@/api/system'
import {
  getPlatformConfigs,
  createPlatformConfig,
  updatePlatformConfig,
  deletePlatformConfig,
  reloadPlatformConfig,
  runCleanupOldData,
  getConfigSchema,
  testMqttConnection,
} from '@/api/platformConfig'
import { useUserStore } from '@/stores/user'
import { useLocaleStore } from '@/stores/locale'
import { useWebSocket, buildWsUrl } from '@/composables/useWebSocket'

const ls = useLocaleStore()
const userStore = useUserStore()
const isSuperuser = computed(() => userStore.userInfo?.is_superuser === true)

// ==================== MQTT 状态 ====================
const mqttInfo = ref({ broker: '', port: '', is_connected: false })
const mqttLoading = ref(false)

// 实时订阅 broker 连/断；建连时 consumer 立刻发一次当前状态
useWebSocket(
  () => buildWsUrl('/ws/system/mqtt/'),
  {
    'system.mqtt': (data) => {
      if (!data) return
      mqttInfo.value = {
        broker: data.broker ?? mqttInfo.value.broker,
        port: data.port ?? mqttInfo.value.port,
        is_connected: !!data.is_connected,
      }
    },
  },
)

async function fetchMqttStatus() {
  mqttLoading.value = true
  try {
    const data = await getMqttStatus()
    mqttInfo.value = data
  } catch {
    mqttInfo.value = { broker: '--', port: '--', is_connected: false }
  } finally {
    mqttLoading.value = false
  }
}

// ==================== schema + 分组表单 ====================
const schemaItems = ref([])         // 来自 GET /platform-configs/schema/
const knownKeys = ref(new Set())
const allConfigs = ref([])          // 来自 GET /platform-configs/
const schemaLoading = ref(false)
const configsLoading = ref(false)

const groupedSchema = ref([])

const CATEGORY_ORDER = ['mqtt', 'devices', 'data_retention', 'general']

function groupLabel(cat) {
  const map = {
    mqtt: ls.t('settings.categoryMqtt'),
    devices: ls.t('settings.categoryDevices'),
    data_retention: ls.t('settings.categoryDataRetention'),
    general: ls.t('settings.categoryGeneral'),
  }
  return map[cat] || cat
}

function fieldLabel(field) {
  // 从 description 取首段（中文逗号/中文括号前），fallback 到 key
  if (field.description) {
    const m = field.description.split(/[（(，,]/)[0]
    return m || field.key
  }
  return field.key
}

function buildGroups() {
  const byCat = {}
  for (const item of schemaItems.value) {
    const cat = item.category || 'general'
    if (!byCat[cat]) byCat[cat] = []
    // 找到当前 DB 中该 key 的值
    const dbRow = allConfigs.value.find((c) => c.key === item.key)
    const currentValue = dbRow ? dbRow.value : item.default
    byCat[cat].push(reactive({
      ...item,
      _value: copyValue(currentValue),
      _original: copyValue(currentValue),
      _editingSecret: false,
      _error: '',
    }))
  }
  // 按预定义顺序输出，未知分类追加
  const groups = []
  for (const cat of CATEGORY_ORDER) {
    if (byCat[cat]) {
      groups.push(reactive({ category: cat, fields: byCat[cat], _saving: false }))
      delete byCat[cat]
    }
  }
  for (const cat of Object.keys(byCat)) {
    groups.push(reactive({ category: cat, fields: byCat[cat], _saving: false }))
  }
  groupedSchema.value = groups
}

function copyValue(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') return JSON.parse(JSON.stringify(v))
  return v
}

function markDirty(field) {
  field._error = ''
}

function toggleSecret(field) {
  field._editingSecret = !field._editingSecret
  if (field._editingSecret) {
    // 进入编辑：清空当前值，避免覆盖
    field._value = ''
  } else {
    // 取消编辑：恢复原值
    field._value = field._original
  }
}

function fieldDirty(field) {
  if (field.secret && !field._editingSecret) return false
  return JSON.stringify(field._value) !== JSON.stringify(field._original)
}

function groupDirty(group) {
  return group.fields.some(fieldDirty)
}

// 首次部署 banner：检测 mqtt_broker 为默认 127.0.0.1
const showFirstTimeBanner = computed(() => {
  const mqttGroup = groupedSchema.value.find((g) => g.category === 'mqtt')
  if (!mqttGroup) return false
  const broker = mqttGroup.fields.find((f) => f.key === 'mqtt_broker')
  if (!broker) return false
  return broker._original === '127.0.0.1' || broker._original === '' || broker._original == null
})

// ==================== schema 拉取 ====================
async function fetchSchema() {
  schemaLoading.value = true
  try {
    const data = await getConfigSchema()
    schemaItems.value = data.items || []
    knownKeys.value = new Set(data.known_keys || [])
  } catch {
    ElMessage.error(ls.t('settings.schemaFetchFailed'))
  } finally {
    schemaLoading.value = false
  }
}

async function fetchAllConfigs() {
  configsLoading.value = true
  try {
    const data = await getPlatformConfigs()
    allConfigs.value = data.results || data || []
  } catch {
    ElMessage.error(ls.t('settings.fetchFailed'))
  } finally {
    configsLoading.value = false
  }
}

const customConfigs = computed(() =>
  allConfigs.value.filter((c) => !knownKeys.value.has(c.key))
)

// ==================== 保存分组 ====================
function validateField(field) {
  field._error = ''
  if (field.type === 'integer' || field.type === 'number') {
    if (field._value === null || field._value === '' || isNaN(Number(field._value))) {
      field._error = ls.t('settings.fieldRequired')
      return false
    }
    if (field.key.endsWith('_port')) {
      const n = Number(field._value)
      if (n < 1 || n > 65535) {
        field._error = ls.t('settings.portRange')
        return false
      }
    }
  }
  // mqtt_broker 不允许空
  if (field.key === 'mqtt_broker' && !String(field._value || '').trim()) {
    field._error = ls.t('settings.fieldRequired')
    return false
  }
  return true
}

async function handleSaveGroup(group) {
  const dirtyFields = group.fields.filter(fieldDirty)
  if (dirtyFields.length === 0) return

  for (const f of dirtyFields) {
    if (!validateField(f)) return
  }

  group._saving = true
  try {
    for (const f of dirtyFields) {
      const dbRow = allConfigs.value.find((c) => c.key === f.key)
      const payload = {
        key: f.key,
        value: f._value,
        category: f.category,
        description: f.description,
      }
      if (dbRow) {
        await updatePlatformConfig(f.key, payload)
      } else {
        await createPlatformConfig(payload)
      }
      f._original = copyValue(f._value)
      if (f.secret) f._editingSecret = false
    }
    ElMessage.success(ls.t('settings.sectionSaved'))
    await fetchAllConfigs()
  } catch (err) {
    const detail = err.response?.data
    const msg = typeof detail === 'object'
      ? (detail.detail || Object.values(detail).flat().join('；'))
      : ls.t('settings.sectionSaveFailed')
    ElMessage.error(msg)
  } finally {
    group._saving = false
  }
}

// ==================== 测试 MQTT ====================
const testingMqtt = ref(false)
async function handleTestMqtt(group) {
  // 用当前表单里的（可能未保存的）值去试连接
  const get = (key) => {
    const f = group.fields.find((x) => x.key === key)
    if (!f) return undefined
    if (f.secret && !f._editingSecret) return f._original  // 没改密码则用原值
    return f._value
  }
  const payload = {
    broker: get('mqtt_broker'),
    port: get('mqtt_port'),
    username: get('mqtt_username'),
    password: get('mqtt_password'),
  }
  testingMqtt.value = true
  try {
    const res = await testMqttConnection(payload)
    if (res.success) {
      ElMessage.success(`${ls.t('settings.testMqttSuccess')}：${res.message || ''}`)
    } else {
      ElMessage.error(`${ls.t('settings.testMqttFailed')}：${res.message || ''}`)
    }
  } catch (err) {
    const detail = err.response?.data
    const msg = typeof detail === 'object' ? (detail.message || detail.detail || '') : ''
    ElMessage.error(`${ls.t('settings.testMqttFailed')}${msg ? '：' + msg : ''}`)
  } finally {
    testingMqtt.value = false
  }
}

// ==================== 高级 / 自定义配置 ====================
const advancedOpen = ref([])
const customDialogVisible = ref(false)
const customSaving = ref(false)
const customFormRef = ref(null)

const customRules = computed(() => ({
  key: [{ required: true, message: ls.t('settings.configKeyRequired'), trigger: 'blur' }],
}))

const customForm = ref({
  id: null,
  key: '',
  value: null,
  valueJson: '',
  category: 'general',
  description: '',
})

function formatValue(val) {
  if (val === null || val === undefined) return '-'
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

function openCustomDialog(row) {
  if (row) {
    customForm.value = {
      id: row.id,
      key: row.key,
      value: row.value,
      valueJson: typeof row.value === 'object' ? JSON.stringify(row.value, null, 2) : String(row.value ?? ''),
      category: row.category || 'general',
      description: row.description || '',
    }
  } else {
    customForm.value = { id: null, key: '', value: null, valueJson: '', category: 'general', description: '' }
  }
  customDialogVisible.value = true
}

async function handleSaveCustom() {
  const formEl = customFormRef.value
  if (formEl) {
    const valid = await formEl.validate().catch(() => false)
    if (!valid) return
  }

  // 不允许把自定义 key 命名成预定义 key
  if (!customForm.value.id && knownKeys.value.has(customForm.value.key)) {
    ElMessage.error(ls.t('settings.predefinedKeyHint', { key: customForm.value.key }))
    return
  }

  let value
  const raw = customForm.value.valueJson.trim()
  if (raw === '') {
    value = null
  } else {
    try {
      value = JSON.parse(raw)
    } catch {
      // 非 JSON 视为字符串
      value = raw
    }
  }

  const payload = {
    key: customForm.value.key,
    value,
    category: customForm.value.category,
    description: customForm.value.description,
  }

  customSaving.value = true
  try {
    if (customForm.value.id) {
      await updatePlatformConfig(customForm.value.key, payload)
      ElMessage.success(ls.t('settings.configUpdated'))
    } else {
      await createPlatformConfig(payload)
      ElMessage.success(ls.t('settings.configCreated'))
    }
    customDialogVisible.value = false
    await fetchAllConfigs()
  } catch (err) {
    const detail = err.response?.data
    const msg = typeof detail === 'object' ? (detail.detail || Object.values(detail).flat().join('；')) : ls.t('settings.saveFailed')
    ElMessage.error(msg)
  } finally {
    customSaving.value = false
  }
}

async function handleDeleteCustom(key) {
  if (knownKeys.value.has(key)) {
    ElMessage.error(ls.t('settings.cannotDeletePredefined'))
    return
  }
  try {
    await deletePlatformConfig(key)
    ElMessage.success(ls.t('settings.deleteSuccess'))
    await fetchAllConfigs()
  } catch {
    ElMessage.error(ls.t('settings.deleteFailed'))
  }
}

// ==================== 命令 ====================
const reloadLoading = ref(false)
const cleanupLoading = ref(false)
const CLEANUP_CONFIRMATION = 'DELETE_EXPIRED_HISTORY'

function cleanupErrorMessage(err) {
  const detail = err?.response?.data
  if (typeof detail === 'object' && detail !== null) {
    return detail.detail || ls.t('settings.cleanupFailed')
  }
  return ls.t('settings.cleanupFailed')
}

async function handleCleanupOldData() {
  if (cleanupLoading.value) return
  cleanupLoading.value = true
  try {
    // 第一步只做试运行：先让管理员看到会受影响的数据，再允许实际删除。
    const preview = await runCleanupOldData({ dry_run: true })
    const previewOutput = typeof preview?.output === 'string' && preview.output.trim()
      ? preview.output.trim()
      : ls.t('settings.cleanupPreviewComplete')

    const confirmMessage = h('div', null, [
      h('p', { style: 'margin: 0 0 12px;' }, ls.t('settings.cleanupPreviewIntro')),
      h('pre', {
        style: 'max-height: 260px; margin: 0 0 14px; padding: 12px; overflow: auto; white-space: pre-wrap; word-break: break-word; border-radius: 6px; background: var(--el-fill-color-light); color: var(--el-text-color-primary); font-family: inherit;',
      }, previewOutput),
      h('p', {
        style: 'margin: 0; font-weight: 600; color: var(--el-color-danger);',
      }, ls.t('settings.cleanupIrreversibleWarning')),
    ])

    try {
      await ElMessageBox.confirm(confirmMessage, ls.t('settings.cleanupPreviewTitle'), {
        type: 'warning',
        confirmButtonText: ls.t('settings.cleanupConfirmDelete'),
        cancelButtonText: ls.t('common.cancel'),
        closeOnClickModal: false,
        distinguishCancelAndClose: true,
      })
    } catch (action) {
      // 关闭或取消确认框属于正常退出，不显示错误提示，也不会发送删除请求。
      if (action === 'cancel' || action === 'close') return
      throw action
    }

    // 第二步必须显式关闭 dry-run，并携带后端要求的不可逆操作确认令牌。
    const res = await runCleanupOldData({
      dry_run: false,
      confirmation: CLEANUP_CONFIRMATION,
    })
    const output = res?.output || ''
    ElMessage.success(output || ls.t('settings.cleanupSuccess'))
  } catch (err) {
    ElMessage.error(cleanupErrorMessage(err))
  } finally {
    cleanupLoading.value = false
  }
}

async function handleReloadConfig() {
  reloadLoading.value = true
  try {
    const res = await reloadPlatformConfig()
    const results = res?.results || {}
    const mqtt = results.mqtt
    if (mqtt === 'reconnected') {
      ElMessage.success(ls.t('settings.mqttReconnected'))
    } else if (mqtt === 'not_running') {
      ElMessage.success(ls.t('settings.mqttNotRunning'))
    } else if (mqtt === 'reconnect_failed') {
      ElMessage.warning(ls.t('settings.mqttReconnectFailed'))
    } else {
      ElMessage.success(ls.t('settings.configApplied'))
    }
    await fetchMqttStatus()
  } catch {
    ElMessage.error(ls.t('settings.applyFailed'))
  } finally {
    reloadLoading.value = false
  }
}

// ==================== 初始化 ====================
onMounted(async () => {
  fetchMqttStatus()
  await Promise.all([fetchSchema(), fetchAllConfigs()])
  buildGroups()
})
</script>

<style scoped>
.section-title {
  font-weight: 600;
  font-size: var(--iot-font-size-md);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-sm);
}

.setting-item {
  display: flex;
  align-items: center;
  padding: var(--iot-spacing-sm) 0;
  border-bottom: 1px solid var(--iot-border-color-light);
}

.setting-item:last-child {
  border-bottom: none;
}

.setting-label {
  width: 140px;
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-sm);
  flex-shrink: 0;
}

.setting-value {
  color: var(--iot-text-primary);
  font-family: 'Courier New', monospace;
}

.config-value {
  font-family: 'Courier New', monospace;
  font-size: var(--iot-font-size-sm);
  word-break: break-all;
}

.config-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--iot-spacing-sm);
}

.empty-hint {
  padding: var(--iot-spacing-lg) 0;
}

.command-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--iot-spacing-md) 0;
  border-bottom: 1px solid var(--iot-border-color-light);
}

.command-item:last-child {
  border-bottom: none;
}

.command-item__info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.command-item__name {
  font-weight: 500;
  color: var(--iot-text-primary);
}

.command-item__desc {
  font-size: var(--iot-font-size-sm);
  color: var(--iot-text-secondary);
}

.field-desc {
  font-size: var(--iot-font-size-sm);
  color: var(--iot-text-secondary);
  margin-top: 8px;
  line-height: 1.5;
  width: 100%;
  flex-basis: 100%;
}

/* 让 description 永远独占一行，与控件留出明显间距 */
:deep(.el-form-item__content) {
  flex-wrap: wrap;
  row-gap: 4px;
}

.secret-row {
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-sm);
}

.secret-placeholder {
  color: var(--iot-text-secondary);
  font-style: italic;
}

.advanced-count {
  margin-left: 8px;
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-sm);
}

.advanced-body {
  padding: var(--iot-spacing-md) 0;
}

.advanced-hint {
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-sm);
  margin: 0 0 var(--iot-spacing-md) 0;
}

.advanced-actions {
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--iot-spacing-md);
}

/* 折叠面板套在 iot-card 里时去掉自带边框，避免双层 */
:deep(.el-collapse) {
  border-top: none;
  border-bottom: none;
}

:deep(.el-collapse-item__header) {
  border-bottom: none;
  background: transparent;
  padding: 0 var(--iot-spacing-md);
  height: 56px;
  font-weight: 600;
}

:deep(.el-collapse-item__wrap) {
  border-bottom: none;
  background: transparent;
}

:deep(.el-collapse-item__content) {
  padding: 0 var(--iot-spacing-md) var(--iot-spacing-md);
  color: var(--iot-text-primary);
}
</style>
