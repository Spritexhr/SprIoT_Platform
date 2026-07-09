<template>
  <div class="automation-view">
    <div class="iot-page-header">
      <div>
        <h1 class="iot-page-title">{{ ls.t('automation.title') }}</h1>
        <p class="iot-page-subtitle">{{ ls.t('automation.subtitle') }}</p>
      </div>
      <el-button v-if="isSuperuser" type="primary" :icon="Plus" @click="openCreateDialog">{{ ls.t('automation.newRule') }}</el-button>
    </div>

    <ResourceFolderBrowser
      ref="folderBrowserRef"
      resource-type="automation"
      :mode="browseMode"
      :current-folder-id="currentFolderId"
      :is-staff="isStaff"
      :dragging-count="draggingIds.length"
      @navigate="handleFolderNavigate"
      @loaded="handleFoldersLoaded"
      @drop-resources="handleFolderDrop"
    />

    <!-- 筛选栏 -->
    <div class="iot-card iot-mb-lg">
      <div class="filter-bar">
        <el-input
          v-model="searchText"
          :placeholder="ls.t('automation.searchPlaceholder')"
          style="width: 300px"
          clearable
          @clear="fetchRules"
          @keyup.enter="fetchRules"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button :icon="Refresh" circle @click="fetchRules" />
        <el-checkbox v-if="isStaff && rules.length" class="select-page-check" :model-value="allPageSelected" @change="toggleSelectPage">
          {{ ls.t('resourceFolders.selectCurrentPage') }}
        </el-checkbox>
        <el-button v-if="isStaff && selectedIds.length" class="selected-move-button" @click="moveDialogVisible = true">
          {{ ls.t('resourceFolders.moveSelected') }}（{{ selectedIds.length }}）
        </el-button>
      </div>
    </div>

    <div class="resource-list-heading">
      <div>
        <h2>{{ currentViewTitle }}</h2>
        <p>{{ ls.t('resourceFolders.listHint') }}</p>
      </div>
      <span v-if="selectedIds.length" class="selected-summary">
        {{ ls.t('resourceFolders.selectedCount').replace('{count}', selectedIds.length) }}
      </span>
    </div>

    <!-- 规则列表 -->
    <div v-loading="loading" class="resource-content">
      <draggable
        v-if="rules.length"
        v-model="rules"
        :item-key="'id'"
        :disabled="!isStaff"
        :sort="canReorder"
        :animation="200"
        ghost-class="drag-ghost"
        chosen-class="drag-chosen"
        drag-class="drag-active"
        handle=".rule-drag-handle, .rule-card"
        class="rules-list"
        @start="handleDragStart"
        @end="handleReorderEnd"
      >
        <template #item="{ element: rule }">
          <div
            class="rule-card-shell"
            :class="{
              'is-selected': selectedIds.includes(rule.id),
              'is-drag-bundle': draggingIds.includes(rule.id),
            }"
            :data-resource-id="rule.id"
          >
            <el-checkbox
              v-if="isStaff"
              class="rule-selector"
              :model-value="selectedIds.includes(rule.id)"
              @click.stop
              @change="(checked) => toggleSelection(rule.id, checked)"
            />
            <span v-if="dragAnchorId === rule.id && draggingIds.length > 1" class="drag-count-badge">
              {{ draggingIds.length }}
            </span>
            <div class="iot-card rule-card">
              <div class="rule-card__header">
                <div class="rule-card__title-group">
                  <div class="rule-card__name" @click="goDetail(rule)">{{ rule.name }}</div>
                  <el-tag v-if="rule.script_id" size="small" type="info" class="rule-card__script-id">
                    {{ rule.script_id }}
                  </el-tag>
                  <el-tag v-if="rule.project" size="small" type="warning" effect="plain">
                    {{ rule.project_code || rule.project_name }} / {{ rule.section_name }}
                  </el-tag>
                  <el-tag
                    :type="getStatusTagType(rule)"
                    size="small"
                    class="rule-card__status"
                  >
                    {{ getStatusText(rule) }}
                  </el-tag>
                </div>
                <div v-if="isStaff || isSuperuser" class="rule-card__actions">
                  <template v-if="isStaff && rule.is_launched && rule.process_status === 'running'">
                    <span class="poll-interval-label">{{ ls.t('automation.runningLabel').replace('{s}', rule.poll_interval || 30) }}</span>
                    <el-button
                      type="danger"
                      size="small"
                      :icon="VideoPause"
                      :loading="launchLoading[rule.id]"
                      @click="handleStop(rule)"
                    >
                      {{ ls.t('automation.stopPoll') }}
                    </el-button>
                  </template>
                  <template v-else-if="isStaff">
                    <el-input-number
                      v-model="rule.poll_interval"
                      :min="1"
                      :max="86400"
                      :step="1"
                      size="small"
                      controls-position="right"
                      class="poll-interval-input"
                    />
                    <span class="poll-interval-unit">{{ ls.t('automation.seconds') }}</span>
                    <el-button
                      type="warning"
                      size="small"
                      :icon="RefreshRight"
                      :loading="launchLoading[rule.id]"
                      @click="handleLaunch(rule)"
                    >
                      {{ ls.t('automation.startPoll') }}
                    </el-button>
                  </template>
                  <el-button
                    v-if="isStaff"
                    type="success"
                    size="small"
                    plain
                    :icon="VideoPlay"
                    :loading="execLoading[rule.id]"
                    @click="handleExecute(rule)"
                  >
                    {{ ls.t('automation.execute') }}
                  </el-button>
                  <el-button
                    v-if="isSuperuser"
                    text
                    size="small"
                    type="danger"
                    :icon="Delete"
                    @click="handleDelete(rule)"
                  />
                </div>
              </div>

              <div v-if="rule.description" class="rule-card__desc">{{ rule.description }}</div>

              <!-- 错误信息 -->
              <div v-if="rule.process_status === 'error_stopped' && rule.error_message" class="rule-card__error">
                <el-alert type="error" :closable="false" show-icon>
                  <template #title>{{ ls.t('automation.errorStopped') }}</template>
                  <span>{{ rule.error_message }}</span>
                </el-alert>
              </div>

              <div class="rule-card__footer">
                <div class="rule-card__meta">
                  <span class="meta-item">
                    <el-icon><Connection /></el-icon>
                    {{ rule.device_count }} {{ ls.t('automation.relatedDevices') }}
                  </span>
                  <span class="meta-item">
                    <el-icon><Clock /></el-icon>
                    {{ formatTime(rule.updated_at) }}
                  </span>
                </div>
                <el-button text size="small" type="primary" @click="goDetail(rule)">
                  {{ ls.t('automation.viewDetail') }} →
                </el-button>
              </div>

              <!-- 执行结果 -->
              <div v-if="execResult[rule.id]" class="rule-card__result">
                <el-alert
                  :title="execResult[rule.id].success ? ls.t('automation.execSuccess') : ls.t('automation.execFailed')"
                  :type="execResult[rule.id].success ? 'success' : 'error'"
                  :closable="true"
                  show-icon
                  @close="execResult[rule.id] = null"
                >
                  <pre v-if="execResult[rule.id].output" class="exec-output">{{ execResult[rule.id].output }}</pre>
                  <span v-if="execResult[rule.id].error" class="exec-error">{{ execResult[rule.id].error }}</span>
                </el-alert>
              </div>
            </div>
          </div>
        </template>
      </draggable>
      <div v-else class="iot-card empty-card">
        <el-empty :description="loading ? '加载中...' : ls.t('automation.noRules')" />
      </div>
    </div>

    <!-- 新建规则弹窗 -->
    <el-dialog v-model="createDialogVisible" :title="ls.t('automation.createDialogTitle')" width="600px" destroy-on-close>
      <el-form :model="createForm" label-width="100px" :rules="createRules" ref="createFormRef">
        <el-form-item :label="ls.t('automation.ruleName')" prop="name">
          <el-input v-model="createForm.name" :placeholder="ls.t('automation.ruleNamePlaceholder')" />
        </el-form-item>
        <el-form-item :label="ls.t('automation.scriptId')" prop="script_id">
          <el-input v-model="createForm.script_id" :placeholder="ls.t('automation.scriptIdPlaceholder')" />
        </el-form-item>
        <el-form-item :label="ls.t('common.description')">
          <el-input v-model="createForm.description" type="textarea" :rows="2" :placeholder="ls.t('automation.descPlaceholder')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">{{ ls.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="createSaving" @click="handleCreate">{{ ls.t('automation.create') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="moveDialogVisible" :title="ls.t('resourceFolders.moveSelected')" width="460px">
      <el-select v-model="moveTargetFolder" clearable :placeholder="ls.t('resourceFolders.unfiled')" style="width: 100%">
        <el-option v-for="option in folderOptions" :key="option.id" :label="option.label" :value="option.id" />
      </el-select>
      <template #footer>
        <el-button @click="moveDialogVisible = false">{{ ls.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="moveSaving" @click="handleBulkMove">{{ ls.t('resourceFolders.confirmMove') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useLocaleStore } from '@/stores/locale'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Refresh, VideoPlay, VideoPause, Delete, Connection, Clock, RefreshRight } from '@element-plus/icons-vue'
import {
  getAutomationRules,
  bulkMoveAutomationRules,
  createAutomationRule,
  deleteAutomationRule,
  executeAutomationRule,
  launchAutomationRule,
  reorderAutomationRules,
  stopAutomationRule,
} from '@/api/automation'
import { useWebSocket, buildWsUrl } from '@/composables/useWebSocket'
import ResourceFolderBrowser from '@/components/resources/ResourceFolderBrowser.vue'
import draggable from 'vuedraggable'

const ls = useLocaleStore()
const router = useRouter()
const userStore = useUserStore()
const isSuperuser = computed(() => userStore.userInfo?.is_superuser === true)
const isStaff = computed(() => userStore.userInfo?.is_staff === true)

// ==================== 筛选 ====================
const searchText = ref('')

// ==================== 文件夹与选择 ====================
const browseMode = ref('folder')
const currentFolderId = ref(null)
const folders = ref([])
const selectedIds = ref([])
const moveDialogVisible = ref(false)
const moveTargetFolder = ref(null)
const moveSaving = ref(false)
const folderBrowserRef = ref(null)
const draggingIds = ref([])
const dragAnchorId = ref(null)
const folderDropActive = ref(false)

const folderMap = computed(() => new Map(folders.value.map((item) => [item.id, item])))
function folderPath(folder) {
  const names = [folder.name]
  let parent = folder.parent ? folderMap.value.get(folder.parent) : null
  while (parent) {
    names.unshift(parent.name)
    parent = parent.parent ? folderMap.value.get(parent.parent) : null
  }
  return names.join(' / ')
}
const folderOptions = computed(() => folders.value.map((item) => ({ ...item, label: folderPath(item) })))
const currentViewTitle = computed(() => {
  if (browseMode.value === 'all') return ls.t('resourceFolders.allResources')
  if (browseMode.value === 'unfiled' || !currentFolderId.value) return ls.t('resourceFolders.unfiledResources')
  return folderMap.value.get(currentFolderId.value)?.name || ls.t('resourceFolders.currentResources')
})

// ==================== 数据 ====================
const rules = ref([])
const loading = ref(false)
const allPageSelected = computed(() => rules.value.length > 0 && rules.value.every((item) => selectedIds.value.includes(item.id)))

async function fetchRules() {
  loading.value = true
  try {
    const params = {}
    if (searchText.value) params.search = searchText.value
    if (browseMode.value === 'unfiled' || (browseMode.value === 'folder' && !currentFolderId.value)) {
      params.folder = 'unfiled'
    } else if (browseMode.value === 'folder' && currentFolderId.value) {
      params.folder = currentFolderId.value
    }
    const data = await getAutomationRules(params)
    rules.value = data.results || data
    selectedIds.value = selectedIds.value.filter((id) => rules.value.some((item) => item.id === id))
  } catch {
    ElMessage.error(ls.t('automation.fetchFailed'))
  } finally {
    loading.value = false
  }
}

function handleFolderNavigate({ mode, folderId }) {
  browseMode.value = mode
  currentFolderId.value = folderId ?? null
  selectedIds.value = []
  fetchRules()
}

function handleFoldersLoaded(items) {
  folders.value = items
}

function toggleSelection(ruleId, checked) {
  if (checked && !selectedIds.value.includes(ruleId)) selectedIds.value.push(ruleId)
  if (!checked) selectedIds.value = selectedIds.value.filter((id) => id !== ruleId)
}

function toggleSelectPage(checked) {
  selectedIds.value = checked ? rules.value.map((item) => item.id) : []
}

async function handleBulkMove() {
  await moveRulesToFolder([...selectedIds.value], moveTargetFolder.value ?? null, true)
}

async function moveRulesToFolder(ids, folderId, closeDialog = false) {
  if (!ids.length) return
  moveSaving.value = true
  try {
    await bulkMoveAutomationRules(ids, folderId)
    ElMessage.success(ls.t('resourceFolders.moved'))
    if (closeDialog) moveDialogVisible.value = false
    selectedIds.value = []
    await fetchRules()
    await folderBrowserRef.value?.refresh()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || ls.t('resourceFolders.moveFailed'))
  } finally {
    moveSaving.value = false
  }
}

async function handleFolderDrop({ folderId }) {
  const ids = [...draggingIds.value]
  if (!ids.length) return
  folderDropActive.value = true
  try {
    await moveRulesToFolder(ids, folderId)
  } finally {
    draggingIds.value = []
    dragAnchorId.value = null
    folderDropActive.value = false
  }
}

const hasActiveFilter = computed(() => Boolean(searchText.value))
const canReorder = computed(() =>
  isStaff.value && browseMode.value === 'folder' && !hasActiveFilter.value
)

function handleDragStart(evt) {
  const rule = rules.value[evt.oldIndex]
  if (!rule) return
  dragAnchorId.value = rule.id
  draggingIds.value = selectedIds.value.includes(rule.id)
    ? [...selectedIds.value]
    : [rule.id]
}

async function handleReorderEnd(evt) {
  if (folderDropActive.value) {
    draggingIds.value = []
    dragAnchorId.value = null
    return
  }
  draggingIds.value = []
  dragAnchorId.value = null
  if (!canReorder.value || (evt && evt.oldIndex === evt.newIndex)) return
  const order = rules.value.map(rule => rule.id)
  try {
    await reorderAutomationRules(order, {
      folder: currentFolderId.value ?? 'unfiled',
    })
  } catch {
    ElMessage.error('排序保存失败')
    fetchRules()
  }
}

// ==================== 状态显示（从后端字段读取） ====================

function getStatusText(rule) {
  const statusMap = {
    idle: ls.t('automation.statusIdle'),
    running: ls.t('automation.statusRunning'),
    stopped_by_user: ls.t('automation.statusStopped'),
    error_stopped: ls.t('automation.statusError'),
  }
  return statusMap[rule.process_status] || ls.t('automation.statusIdle')
}

function getStatusTagType(rule) {
  const typeMap = {
    idle: 'info',
    running: 'success',
    stopped_by_user: 'info',
    error_stopped: 'danger',
  }
  return typeMap[rule.process_status] || 'info'
}

// ==================== 轮询控制（后端状态） ====================
const launchLoading = ref({})

async function handleLaunch(rule) {
  launchLoading.value[rule.id] = true
  try {
    const res = await launchAutomationRule(rule.id, rule.poll_interval)
    rule.is_launched = res.is_launched
    rule.process_status = res.process_status
    rule.poll_interval = res.poll_interval
    rule.error_message = ''
    ElMessage.success(`${rule.name} - ${ls.t('automation.launchSuccess')}`)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || ls.t('automation.launchFailed'))
  } finally {
    launchLoading.value[rule.id] = false
  }
}

async function handleStop(rule) {
  launchLoading.value[rule.id] = true
  try {
    const res = await stopAutomationRule(rule.id, 'user')
    rule.is_launched = res.is_launched
    rule.process_status = res.process_status
    rule.error_message = ''
    ElMessage.success(`${rule.name} - ${ls.t('automation.stopSuccess')}`)
  } catch {
    ElMessage.error(ls.t('automation.stopFailed'))
  } finally {
    launchLoading.value[rule.id] = false
  }
}

// ==================== 实时状态推送 ====================
// 任何规则 is_launched / process_status / error_message 变化，后端 post_save signal
// → /ws/automation/ → 这里 patch 本地行；新增的规则 created=true，也插到列表顶部
function onRuleEvent(data) {
  if (!data || data.id == null) return
  const existing = rules.value.find(r => r.id === data.id)
  if (existing) {
    existing.is_launched = data.is_launched
    existing.process_status = data.process_status
    existing.error_message = data.error_message
    if (data.poll_interval != null) existing.poll_interval = data.poll_interval
    if (data.updated_at) existing.updated_at = data.updated_at
  } else if (data.created) {
    // 别的客户端新建的规则；前端列表里没有，丢个轻量刷新拿全字段（包含 device_list 等）
    fetchRules()
  }
}

useWebSocket(
  () => buildWsUrl('/ws/automation/'),
  { 'automation.rule': onRuleEvent },
)

// ==================== 手动执行 ====================
const execLoading = ref({})
const execResult = ref({})

async function handleExecute(rule) {
  execLoading.value[rule.id] = true
  execResult.value[rule.id] = null
  try {
    const res = await executeAutomationRule(rule.id)
    execResult.value[rule.id] = res
  } catch (err) {
    execResult.value[rule.id] = {
      success: false,
      error: err.response?.data?.detail || err.response?.data?.error || 'execution error',
      output: err.response?.data?.output || '',
    }
  } finally {
    execLoading.value[rule.id] = false
  }
}

// ==================== 删除 ====================
async function handleDelete(rule) {
  try {
    await ElMessageBox.confirm(
      ls.t('automation.deleteConfirmMsg').replace('{name}', rule.name),
      ls.t('automation.deleteConfirmTitle'),
      { type: 'warning', confirmButtonText: ls.t('common.deleteConfirmOk'), cancelButtonText: ls.t('common.cancel') }
    )
  } catch {
    return
  }
  try {
    await deleteAutomationRule(rule.id)
    ElMessage.success(ls.t('automation.deleted'))
    await fetchRules()
    await folderBrowserRef.value?.refresh()
  } catch {
    ElMessage.error(ls.t('automation.deleteFailed'))
  }
}

// ==================== 跳转详情 ====================
function goDetail(rule) {
  router.push({ name: 'AutomationDetail', params: { id: rule.id } })
}

// ==================== 新建规则 ====================
const createDialogVisible = ref(false)
const createSaving = ref(false)
const createFormRef = ref(null)
const createForm = ref({
  name: '',
  script_id: '',
  description: '',
})

const createRules = computed(() => ({
  name: [{ required: true, message: ls.t('automation.ruleNameRequired'), trigger: 'blur' }],
  script_id: [
    { required: true, message: ls.t('automation.scriptIdRequired'), trigger: 'blur' },
    { pattern: /^[a-zA-Z0-9_]+$/, message: ls.t('automation.scriptIdPattern'), trigger: 'blur' },
  ],
}))

function openCreateDialog() {
  createForm.value = { name: '', script_id: '', description: '' }
  createDialogVisible.value = true
}

async function handleCreate() {
  const formEl = createFormRef.value
  if (formEl) {
    const valid = await formEl.validate().catch(() => false)
    if (!valid) return
  }
  createSaving.value = true
  try {
    await createAutomationRule({
      ...createForm.value,
      script: '',
      device_list: [],
      folder: browseMode.value === 'folder' ? currentFolderId.value : null,
    })
    createDialogVisible.value = false
    ElMessage.success(ls.t('automation.ruleCreated'))
    await fetchRules()
    await folderBrowserRef.value?.refresh()
  } catch (err) {
    const detail = err.response?.data
    const msg = typeof detail === 'object' ? Object.values(detail).flat().join('；') : ls.t('automation.createFailed')
    ElMessage.error(msg)
  } finally {
    createSaving.value = false
  }
}

// ==================== 工具函数 ====================
function formatTime(str) {
  if (!str) return '--'
  const d = new Date(str)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ==================== 初始化 ====================
// WS 在 setup 顶层已建立；onScopeDispose 会在组件卸载时自动 stop
onMounted(() => {
  fetchRules()
})
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-md);
  padding: var(--iot-spacing-md) var(--iot-spacing-lg);
  flex-wrap: wrap;
}

.resource-list-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin: 2px 2px 14px;
}

.resource-list-heading h2 {
  margin: 0;
  color: var(--iot-text-primary);
  font-size: 18px;
}

.resource-list-heading p {
  margin: 5px 0 0;
  color: var(--iot-text-secondary);
  font-size: 12px;
}

.selected-summary {
  flex: 0 0 auto;
  padding: 6px 11px;
  border: 1px solid color-mix(in srgb, var(--iot-color-primary) 28%, transparent);
  border-radius: 999px;
  color: var(--iot-color-primary-dark);
  background: var(--iot-color-primary-bg);
  font-size: 12px;
  font-weight: 600;
}

.selected-move-button {
  border-color: color-mix(in srgb, var(--iot-color-primary) 36%, transparent);
  color: var(--iot-color-primary-dark);
  background: var(--iot-color-primary-bg);
}

.select-page-check {
  padding: 0 4px;
}

.resource-content {
  min-height: 140px;
}

.rules-list {
  display: flex;
  flex-direction: column;
  gap: var(--iot-spacing-md);
}

.rule-card-shell {
  position: relative;
  min-width: 0;
  border-radius: var(--iot-radius-lg);
  transition: transform .18s, filter .18s;
}

.rule-selector {
  position: absolute;
  z-index: 6;
  top: 16px;
  left: 16px;
  display: grid;
  width: 22px;
  height: 22px;
  margin: 0;
  padding: 0;
  place-items: center;
  border: 1px solid var(--iot-border-color-light);
  border-radius: 7px;
  background: color-mix(in srgb, var(--iot-bg-card) 96%, var(--iot-bg-page));
  box-shadow: none;
}

.rule-selector :deep(.el-checkbox__input) {
  display: inline-flex;
}

.rule-selector :deep(.el-checkbox__label) {
  display: none;
}

.rule-card-shell.is-selected .rule-card {
  border-color: var(--iot-color-primary);
  background: linear-gradient(145deg, var(--iot-bg-card), var(--iot-color-primary-bg));
  box-shadow: 0 0 0 2px var(--iot-color-primary-bg), var(--iot-shadow-md);
}

.rule-card-shell.is-selected::after {
  content: '';
  position: absolute;
  inset: 0;
  border: 1px solid color-mix(in srgb, var(--iot-color-primary) 42%, transparent);
  border-radius: var(--iot-radius-lg);
  pointer-events: none;
}

.rule-card-shell.is-drag-bundle {
  filter: saturate(1.04);
}

.drag-count-badge {
  position: absolute;
  z-index: 9;
  top: -9px;
  right: -7px;
  display: grid;
  min-width: 26px;
  height: 26px;
  padding: 0 7px;
  place-items: center;
  border: 2px solid var(--iot-bg-card);
  border-radius: 999px;
  color: white;
  background: var(--iot-color-primary);
  box-shadow: 0 5px 14px color-mix(in srgb, var(--iot-color-primary) 28%, transparent);
  font-size: 12px;
  font-weight: 700;
}

.rule-card {
  padding: var(--iot-spacing-lg);
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: opacity var(--iot-transition-base);
}

.rule-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--iot-spacing-md);
  flex-wrap: wrap;
}

.rule-card__title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 24px;
  padding-left: 34px;
}

.rule-card__name {
  font-size: var(--iot-font-size-md);
  font-weight: 600;
  color: var(--iot-text-primary);
  cursor: pointer;
  transition: color var(--iot-transition-fast);
}

.rule-card__name:hover {
  color: var(--iot-color-primary);
}

.rule-card__script-id {
  font-family: 'Courier New', monospace;
  font-size: 11px;
}

.rule-card__status {
  margin-left: 4px;
}

.rule-card__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.poll-interval-input {
  width: 100px;
}

.poll-interval-unit {
  font-size: 12px;
  color: var(--iot-text-secondary);
  margin-right: 2px;
}

.poll-interval-label {
  font-size: 12px;
  color: var(--iot-text-secondary);
  white-space: nowrap;
}

.rule-card__desc {
  font-size: var(--iot-font-size-sm);
  color: var(--iot-text-secondary);
  line-height: 1.5;
}

.rule-card__error {
  margin-top: 2px;
}

.rule-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid var(--iot-border-color-lighter);
}

.rule-card__meta {
  display: flex;
  gap: var(--iot-spacing-lg);
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--iot-font-size-xs);
  color: var(--iot-text-secondary);
}

.rule-card__result {
  margin-top: 4px;
}

.exec-output {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 4px 0 0;
  max-height: 120px;
  overflow-y: auto;
}

.exec-error {
  color: var(--iot-color-danger);
  font-size: 12px;
}

.empty-card {
  padding: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.drag-ghost {
  opacity: 0.34;
  background: var(--iot-color-primary-bg);
  border: 2px dashed var(--iot-color-primary);
  border-radius: var(--iot-radius-base);
}

.drag-chosen {
  transform: translateY(-2px);
}

.drag-active {
  transform: rotate(.35deg) scale(1.01);
  filter: drop-shadow(0 14px 20px rgba(79, 53, 38, .16));
}

@media (max-width: 700px) {
  .resource-list-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .filter-bar :deep(.el-input) {
    width: 100% !important;
  }

  .rule-card__actions {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
