<template>
  <div class="sensors-view">
    <div class="iot-page-header">
      <div>
        <h1 class="iot-page-title">{{ ls.t('sensors.title') }}</h1>
        <p class="iot-page-subtitle">{{ ls.t('sensors.subtitle') }}</p>
      </div>
    </div>

    <!-- ==================== 传感器类型管理 ==================== -->
    <details class="iot-card iot-mb-lg type-details">
      <summary class="type-summary">{{ ls.t('sensors.typeManagement') }}<span>{{ ls.t('resourceFolders.expandConfig') }}</span></summary>
      <div class="iot-card__header">
        <span class="section-title">{{ ls.t('sensors.typeManagement') }}</span>
        <el-button v-if="isStaff" type="primary" :icon="Plus" @click="openSensorTypeDialog(null)">
          {{ ls.t('sensors.addType') }}
        </el-button>
      </div>
      <div class="iot-card__body">
        <el-table :data="sensorTypes" v-loading="sensorTypesLoading" stripe>
          <el-table-column prop="SensorType_id" :label="ls.t('common.typeId')" width="160" />
          <el-table-column prop="name" :label="ls.t('common.name')" width="180" />
          <el-table-column :label="ls.t('sensors.dataFields')" min-width="200">
            <template #default="{ row }">
              <el-tag
                v-for="field in (row.data_fields || [])"
                :key="field"
                size="small"
                class="field-tag"
              >
                {{ field }}
              </el-tag>
              <span v-if="!row.data_fields || row.data_fields.length === 0" class="iot-text-secondary">-</span>
            </template>
          </el-table-column>
          <el-table-column :label="ls.t('common.cmdCount')" width="80" align="center">
            <template #default="{ row }">
              {{ row.commands ? Object.keys(row.commands).length : 0 }}
            </template>
          </el-table-column>
          <el-table-column :label="ls.t('sensors.relatedSensors')" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ row.sensor_count || 0 }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="isStaff" :label="ls.t('common.operations')" width="160" align="center" fixed="right">
            <template #default="{ row }">
              <el-button text type="primary" size="small" @click="openSensorTypeDialog(row)">
                {{ ls.t('common.edit') }}
              </el-button>
              <el-popconfirm
                :title="ls.t('sensors.deleteTypeConfirm')"
                :confirm-button-text="ls.t('common.delete')"
                :cancel-button-text="ls.t('common.cancel')"
                @confirm="handleDeleteSensorType(row.id)"
              >
                <template #reference>
                  <el-button text type="danger" size="small">{{ ls.t('common.delete') }}</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </details>

    <ResourceFolderBrowser
      ref="folderBrowserRef"
      resource-type="sensor"
      :mode="browseMode"
      :current-folder-id="currentFolderId"
      :is-staff="isStaff"
      :dragging-count="draggingIds.length"
      @navigate="handleFolderNavigate"
      @loaded="handleFoldersLoaded"
      @drop-resources="handleFolderDrop"
    />

    <!-- 筛选栏 -->
    <div class="iot-card iot-mb-lg resource-tools-card">
      <div class="filter-bar">
        <el-select v-model="filterType" :placeholder="ls.t('sensors.filterType')" style="width: 200px" clearable @change="handleFilterChange">
          <el-option :label="ls.t('common.allTypes')" value="" />
          <el-option
            v-for="t in sensorTypes"
            :key="t.id"
            :label="t.name"
            :value="t.id"
          />
        </el-select>
        <el-select v-model="filterOnline" :placeholder="ls.t('sensors.filterStatus')" style="width: 140px" clearable @change="handleFilterChange">
          <el-option :label="ls.t('common.all')" value="" />
          <el-option :label="ls.t('common.online')" value="true" />
          <el-option :label="ls.t('common.offline')" value="false" />
        </el-select>
        <el-input
          v-model="searchText"
          :placeholder="ls.t('sensors.searchPlaceholder')"
          style="width: 260px"
          clearable
          @clear="handleFilterChange"
          @keyup.enter="handleFilterChange"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button :icon="Refresh" circle @click="fetchSensors" />
        <el-checkbox v-if="isStaff && sensors.length" class="select-page-check" :model-value="allPageSelected" @change="toggleSelectPage">
          {{ ls.t('resourceFolders.selectCurrentPage') }}
        </el-checkbox>
        <el-button v-if="isStaff && selectedIds.length" class="selected-move-button" @click="moveDialogVisible = true">
          {{ ls.t('resourceFolders.moveSelected') }}（{{ selectedIds.length }}）
        </el-button>
        <el-button v-if="isStaff && selectedIds.length" type="danger" plain :icon="Delete" @click="handleBulkDeleteSensors">
          删除选中（{{ selectedIds.length }}）
        </el-button>
        <el-button v-if="isStaff && sensors.length" type="primary" :icon="Plus" @click="openAddDialog">{{ ls.t('sensors.addSensor') }}</el-button>
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

    <!-- 卡片网格 -->
    <div v-loading="loading" class="resource-content">
      <draggable
        v-if="sensors.length"
        v-model="sensors"
        :item-key="'sensor_id'"
        :disabled="!isStaff"
        :sort="canReorder"
        :animation="200"
        ghost-class="drag-ghost"
        chosen-class="drag-chosen"
        drag-class="drag-active"
        handle=".sensor-drag-handle, .iot-card"
        class="iot-grid iot-grid--cards"
        @start="handleDragStart"
        @end="handleReorderEnd"
      >
        <template #item="{ element: s }">
          <div
            class="resource-card-shell"
            :class="{
              'is-selected': selectedIds.includes(s.sensor_id),
              'is-drag-bundle': draggingIds.includes(s.sensor_id),
            }"
            :data-resource-id="s.sensor_id"
          >
            <el-checkbox
              v-if="isStaff"
              class="resource-selector"
              :model-value="selectedIds.includes(s.sensor_id)"
              @click.stop
              @change="(checked) => toggleSelection(s.sensor_id, checked)"
            />
            <span v-if="dragAnchorId === s.sensor_id && draggingIds.length > 1" class="drag-count-badge">
              {{ draggingIds.length }}
            </span>
            <SensorCard :sensor="s" :show-delete="false" @click="goDetail(s)" />
          </div>
        </template>
      </draggable>
      <div v-if="!sensors.length" class="iot-card empty-card">
        <el-empty :description="loading ? ls.t('common.loading') : ls.t('sensors.noSensors')">
          <el-button v-if="isStaff" type="primary" :icon="Plus" @click="openAddDialog">{{ ls.t('sensors.addSensor') }}</el-button>
        </el-empty>
      </div>
      <div v-if="total > 0" class="pagination-row">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[24, 48, 96]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          @current-change="fetchSensors"
          @size-change="handlePageSizeChange"
        />
      </div>
    </div>

    <!-- 传感器类型编辑弹窗 -->
    <el-dialog
      v-model="sensorTypeDialogVisible"
      :title="sensorTypeForm.id ? ls.t('sensors.editTypeTitle') : ls.t('sensors.addTypeTitle')"
      width="600px"
      destroy-on-close
    >
      <el-form :model="sensorTypeForm" label-width="100px" :rules="sensorTypeRules" ref="sensorTypeFormRef">
        <el-form-item :label="ls.t('sensors.typeIdLabel')" prop="SensorType_id">
          <el-input v-model="sensorTypeForm.SensorType_id" :placeholder="ls.t('sensors.typeIdPlaceholder')" />
        </el-form-item>
        <el-form-item :label="ls.t('common.name')" prop="name">
          <el-input v-model="sensorTypeForm.name" :placeholder="ls.t('sensors.namePlaceholder')" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="sensorTypeForm.description" type="textarea" :rows="2" :placeholder="ls.t('sensors.descPlaceholder')" />
        </el-form-item>
        <el-form-item :label="ls.t('sensors.dataFields')">
          <el-select
            v-model="sensorTypeForm.data_fields"
            multiple
            filterable
            allow-create
            default-first-option
            :placeholder="ls.t('sensors.dataFieldsPlaceholder')"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item :label="ls.t('sensors.configParamsLabel')">
          <el-select
            v-model="sensorTypeForm.config_parameters"
            multiple
            filterable
            allow-create
            default-first-option
            :placeholder="ls.t('sensors.configParamsPlaceholder')"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item :label="ls.t('sensors.commandsLabel')">
          <div class="commands-editor">
            <el-input
              v-model="sensorTypeForm.commands_json"
              type="textarea"
              :rows="6"
              :placeholder="ls.t('sensors.commandsPlaceholder')"
            />
            <div v-if="sensorTypeCmdError" class="cmd-error">{{ sensorTypeCmdError }}</div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sensorTypeDialogVisible = false">{{ ls.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="sensorTypeSaving" @click="handleSaveSensorType">
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 添加传感器弹窗 -->
    <el-dialog v-model="addDialogVisible" :title="ls.t('sensors.addDialogTitle')" width="500px" destroy-on-close>
      <el-form :model="addForm" label-width="100px" :rules="addRules" ref="addFormRef">
        <el-form-item :label="ls.t('sensors.sensorIdLabel')" prop="sensor_id">
          <el-input v-model="addForm.sensor_id" :placeholder="ls.t('sensors.sensorIdPlaceholder')" />
        </el-form-item>
        <el-form-item :label="ls.t('common.name')" prop="name">
          <el-input v-model="addForm.name" :placeholder="ls.t('sensors.sensorNamePlaceholder')" />
        </el-form-item>
        <el-form-item label="传感器类型" prop="sensor_type">
          <el-select v-model="addForm.sensor_type" placeholder="选择类型" style="width: 100%">
            <el-option
              v-for="t in sensorTypes"
              :key="t.id"
              :label="t.name"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="ls.t('resourceFolders.folder')">
          <el-select v-model="addForm.folder" clearable :placeholder="ls.t('resourceFolders.unfiled')" style="width: 100%">
            <el-option v-for="option in folderOptions" :key="option.id" :label="option.label" :value="option.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="位置">
          <el-input v-model="addForm.location" :placeholder="ls.t('sensors.locationPlaceholder')" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="addForm.description" type="textarea" :rows="2" :placeholder="ls.t('common.optional')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialogVisible = false">{{ ls.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="addSaving" @click="handleAddSensor">保存</el-button>
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
import { Delete, Plus, Search, Refresh } from '@element-plus/icons-vue'
import { getSensors, createSensor, deleteSensor, getSensorTypes, createSensorType, updateSensorType, deleteSensorType, reorderSensors, bulkMoveSensors } from '@/api/sensors'
import SensorCard from '@/components/sensors/SensorCard.vue'
import ResourceFolderBrowser from '@/components/resources/ResourceFolderBrowser.vue'
import draggable from 'vuedraggable'
import { useWebSocket, buildWsUrl } from '@/composables/useWebSocket'

const router = useRouter()
const userStore = useUserStore()
const ls = useLocaleStore()
const isStaff = computed(() => userStore.userInfo?.is_staff === true)

// ==================== 筛选 ====================
const filterType = ref('')
const filterOnline = ref('')
const searchText = ref('')

// ==================== 文件夹与分页 ====================
const browseMode = ref('folder')
const currentFolderId = ref(null)
const folders = ref([])
const page = ref(1)
const pageSize = ref(24)
const total = ref(0)
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
const sensors = ref([])
const sensorTypes = ref([])
const loading = ref(false)
const sensorTypesLoading = ref(false)
const allPageSelected = computed(() => sensors.value.length > 0 && sensors.value.every((item) => selectedIds.value.includes(item.sensor_id)))

async function fetchSensors() {
  loading.value = true
  try {
    const params = {}
    params.page = page.value
    params.page_size = pageSize.value
    if (filterType.value) params.sensor_type = filterType.value
    if (filterOnline.value) params.online = filterOnline.value
    if (searchText.value) params.search = searchText.value
    if (browseMode.value === 'unfiled' || (browseMode.value === 'folder' && !currentFolderId.value)) {
      params.folder = 'unfiled'
    } else if (browseMode.value === 'folder' && currentFolderId.value) {
      params.folder = currentFolderId.value
    }
    const data = await getSensors(params)
    sensors.value = data.results || data
    total.value = data.count ?? sensors.value.length
    selectedIds.value = selectedIds.value.filter((id) => sensors.value.some((item) => item.sensor_id === id))
  } catch {
    ElMessage.error(ls.t('sensors.fetchListFailed'))
  } finally {
    loading.value = false
  }
}

function handleFilterChange() {
  page.value = 1
  selectedIds.value = []
  fetchSensors()
}

function handlePageSizeChange() {
  page.value = 1
  selectedIds.value = []
  fetchSensors()
}

function handleFolderNavigate({ mode, folderId }) {
  browseMode.value = mode
  currentFolderId.value = folderId ?? null
  page.value = 1
  selectedIds.value = []
  fetchSensors()
}

function handleFoldersLoaded(items) {
  folders.value = items
}

function toggleSelection(sensorId, checked) {
  if (checked && !selectedIds.value.includes(sensorId)) selectedIds.value.push(sensorId)
  if (!checked) selectedIds.value = selectedIds.value.filter((id) => id !== sensorId)
}

function toggleSelectPage(checked) {
  selectedIds.value = checked ? sensors.value.map((item) => item.sensor_id) : []
}

async function handleBulkMove() {
  await moveSensorsToFolder([...selectedIds.value], moveTargetFolder.value ?? null, true)
}

async function moveSensorsToFolder(ids, folderId, closeDialog = false) {
  if (!ids.length) return
  moveSaving.value = true
  try {
    await bulkMoveSensors(ids, folderId)
    ElMessage.success(ls.t('resourceFolders.moved'))
    if (closeDialog) moveDialogVisible.value = false
    selectedIds.value = []
    await fetchSensors()
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
    await moveSensorsToFolder(ids, folderId)
  } finally {
    draggingIds.value = []
    dragAnchorId.value = null
    folderDropActive.value = false
  }
}

async function fetchSensorTypeList() {
  sensorTypesLoading.value = true
  try {
    const data = await getSensorTypes()
    sensorTypes.value = data.results || data
  } catch {
    ElMessage.error(ls.t('sensors.fetchTypesFailed'))
  } finally {
    sensorTypesLoading.value = false
  }
}

// ==================== 传感器类型管理 ====================
const sensorTypeDialogVisible = ref(false)
const sensorTypeSaving = ref(false)
const sensorTypeFormRef = ref(null)
const sensorTypeCmdError = ref('')

const sensorTypeRules = computed(() => ({
  SensorType_id: [{ required: true, message: ls.t('sensors.typeIdRequired'), trigger: 'blur' }],
  name: [{ required: true, message: ls.t('sensors.nameRequired'), trigger: 'blur' }],
}))

const emptySensorTypeForm = () => ({
  id: null,
  SensorType_id: '',
  name: '',
  description: '',
  data_fields: [],
  config_parameters: [],
  commands_json: '{}',
})

const sensorTypeForm = ref(emptySensorTypeForm())

function openSensorTypeDialog(row) {
  sensorTypeCmdError.value = ''
  if (row) {
    sensorTypeForm.value = {
      id: row.id,
      SensorType_id: row.SensorType_id,
      name: row.name,
      description: row.description || '',
      data_fields: row.data_fields || [],
      config_parameters: row.config_parameters || [],
      commands_json: JSON.stringify(row.commands || {}, null, 2),
    }
  } else {
    sensorTypeForm.value = emptySensorTypeForm()
  }
  sensorTypeDialogVisible.value = true
}

async function handleSaveSensorType() {
  const formEl = sensorTypeFormRef.value
  if (formEl) {
    const valid = await formEl.validate().catch(() => false)
    if (!valid) return
  }

  let commands
  try {
    commands = JSON.parse(sensorTypeForm.value.commands_json)
    sensorTypeCmdError.value = ''
  } catch {
    sensorTypeCmdError.value = ls.t('common.jsonError')
    return
  }

  const payload = {
    SensorType_id: sensorTypeForm.value.SensorType_id,
    name: sensorTypeForm.value.name,
    description: sensorTypeForm.value.description,
    data_fields: sensorTypeForm.value.data_fields,
    config_parameters: sensorTypeForm.value.config_parameters,
    commands: commands,
  }

  sensorTypeSaving.value = true
  try {
    if (sensorTypeForm.value.id) {
      await updateSensorType(sensorTypeForm.value.id, payload)
      ElMessage.success(ls.t('sensors.typeUpdated'))
    } else {
      await createSensorType(payload)
      ElMessage.success(ls.t('sensors.typeCreated'))
    }
    sensorTypeDialogVisible.value = false
    fetchSensorTypeList()
  } catch (err) {
    const detail = err.response?.data
    const msg = typeof detail === 'object' ? Object.values(detail).flat().join('；') : ls.t('common.saveFailed')
    ElMessage.error(msg)
  } finally {
    sensorTypeSaving.value = false
  }
}

async function handleDeleteSensorType(id) {
  try {
    await deleteSensorType(id)
    ElMessage.success(ls.t('common.deleteSuccess'))
    fetchSensorTypeList()
  } catch {
    ElMessage.error(ls.t('sensors.deleteTypeFailed'))
  }
}

// ==================== 拖拽排序 ====================
const hasActiveFilter = computed(() =>
  Boolean(filterType.value || filterOnline.value || searchText.value)
)
const canReorder = computed(() =>
  isStaff.value && browseMode.value === 'folder' && !hasActiveFilter.value
)

function handleDragStart(evt) {
  const sensor = sensors.value[evt.oldIndex]
  if (!sensor) return
  dragAnchorId.value = sensor.sensor_id
  draggingIds.value = selectedIds.value.includes(sensor.sensor_id)
    ? [...selectedIds.value]
    : [sensor.sensor_id]
}

async function handleReorderEnd(evt) {
  if (folderDropActive.value) {
    draggingIds.value = []
    dragAnchorId.value = null
    return
  }
  draggingIds.value = []
  dragAnchorId.value = null
  // 顺序未变就不发请求
  if (!canReorder.value || (evt && evt.oldIndex === evt.newIndex)) return
  const order = sensors.value.map(s => s.sensor_id)
  try {
    await reorderSensors(order, {
      page: page.value,
      page_size: pageSize.value,
      folder: currentFolderId.value ?? 'unfiled',
    })
  } catch {
    ElMessage.error(ls.t('sensors.reorderFailed') || '排序保存失败')
    fetchSensors()
  }
}

// ==================== 跳转详情 ====================
function goDetail(sensor) {
  router.push({ name: 'SensorDetail', params: { sensorId: sensor.sensor_id } })
}

// ==================== 删除传感器 ====================
async function handleDeleteSensor(sensor) {
  try {
    await ElMessageBox.confirm(
      ls.t('sensors.deleteConfirmMsg').replace('{name}', sensor.name),
      ls.t('common.deleteConfirmTitle'),
      { confirmButtonText: ls.t('common.deleteConfirmOk'), cancelButtonText: ls.t('common.cancel'), type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await deleteSensor(sensor.sensor_id)
    ElMessage.success(ls.t('sensors.sensorDeleted'))
    fetchSensors()
    folderBrowserRef.value?.refresh()
  } catch {
    ElMessage.error(ls.t('common.deleteFailed'))
  }
}

async function handleBulkDeleteSensors() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确认删除已选中的 ${ids.length} 个传感器对象？此操作不可恢复。`,
      ls.t('common.deleteConfirmTitle'),
      { confirmButtonText: ls.t('common.deleteConfirmOk'), cancelButtonText: ls.t('common.cancel'), type: 'warning' }
    )
  } catch {
    return
  }
  loading.value = true
  try {
    await Promise.all(ids.map((sensorId) => deleteSensor(sensorId)))
    ElMessage.success(`已删除 ${ids.length} 个传感器`)
    selectedIds.value = []
    await fetchSensors()
    await folderBrowserRef.value?.refresh()
  } catch {
    ElMessage.error(ls.t('common.deleteFailed'))
  } finally {
    loading.value = false
  }
}

// ==================== 添加传感器 ====================
const addDialogVisible = ref(false)
const addSaving = ref(false)
const addFormRef = ref(null)
const addForm = ref({
  sensor_id: '',
  name: '',
  sensor_type: null,
  location: '',
  description: '',
  folder: null,
})

const addRules = computed(() => ({
  sensor_id: [{ required: true, message: ls.t('sensors.sensorIdRequired'), trigger: 'blur' }],
  name: [{ required: true, message: ls.t('sensors.sensorNameRequired'), trigger: 'blur' }],
  sensor_type: [{ required: true, message: ls.t('sensors.sensorTypeRequired'), trigger: 'change' }],
}))

function openAddDialog() {
  const defaultFolder = browseMode.value === 'folder' ? currentFolderId.value : null
  addForm.value = { sensor_id: '', name: '', sensor_type: null, location: '', description: '', folder: defaultFolder }
  addDialogVisible.value = true
}

async function handleAddSensor() {
  const formEl = addFormRef.value
  if (formEl) {
    const valid = await formEl.validate().catch(() => false)
    if (!valid) return
  }
  addSaving.value = true
  try {
    await createSensor(addForm.value)
    ElMessage.success(ls.t('sensors.sensorAdded'))
    addDialogVisible.value = false
    fetchSensors()
    folderBrowserRef.value?.refresh()
  } catch (err) {
    const detail = err.response?.data
    const msg = typeof detail === 'object' ? Object.values(detail).flat().join('；') : ls.t('common.addFailed')
    ElMessage.error(msg)
  } finally {
    addSaving.value = false
  }
}

// ==================== 实时推送 ====================
// 订阅全量传感器 channel；按 sensor_id 找到本地卡片，patch 最新值/在线状态
function onSensorData(data) {
  if (!data || !data.sensor_id) return
  const row = sensors.value.find(s => s.sensor_id === data.sensor_id)
  if (!row) return
  const tsIso = data.timestamp ? new Date(data.timestamp * 1000).toISOString() : null
  row.latest_data = {
    data: data.data,
    timestamp: tsIso,
    received_at: data.received_at ? new Date(data.received_at * 1000).toISOString() : tsIso,
  }
  // 收到数据 = 在线
  row.is_online = true
  row.last_seen = tsIso
}

function onSensorStatus(data) {
  if (!data || !data.sensor_id) return
  const row = sensors.value.find(s => s.sensor_id === data.sensor_id)
  if (!row) return
  row.is_online = !!data.is_online
  if (data.last_seen) row.last_seen = new Date(data.last_seen * 1000).toISOString()
}

useWebSocket(
  () => buildWsUrl('/ws/sensors/'),
  {
    'sensor.data': onSensorData,
    'sensor.status': onSensorStatus,
  },
)

// ==================== 初始化 ====================
onMounted(() => {
  fetchSensors()
  fetchSensorTypeList()
})
</script>

<style scoped>
.section-title {
  font-weight: 600;
  font-size: var(--iot-font-size-md);
}

.type-details { overflow: hidden; border-color: var(--iot-border-color-light); box-shadow: none; }
.type-summary { display: flex; align-items: center; justify-content: space-between; padding: 17px 20px; color: var(--iot-text-regular); font-weight: 600; cursor: pointer; list-style-position: inside; transition: background .18s; }
.type-summary:hover { background: var(--iot-bg-card-hover); }
.type-summary span { color: var(--iot-text-secondary); font-size: 12px; font-weight: 400; }
.resource-tools-card { overflow: hidden; border-color: var(--iot-border-color-light); box-shadow: var(--iot-shadow-sm); }
.resource-list-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin: 2px 2px 14px; }
.resource-list-heading h2 { margin: 0; color: var(--iot-text-primary); font-size: 18px; }
.resource-list-heading p { margin: 5px 0 0; color: var(--iot-text-secondary); font-size: 12px; }
.selected-summary { flex: 0 0 auto; padding: 6px 11px; border: 1px solid color-mix(in srgb, var(--iot-color-primary) 28%, transparent); border-radius: 999px; color: var(--iot-color-primary-dark); background: var(--iot-color-primary-bg); font-size: 12px; font-weight: 600; }
.selected-move-button { border-color: color-mix(in srgb, var(--iot-color-primary) 36%, transparent); color: var(--iot-color-primary-dark); background: var(--iot-color-primary-bg); }
.select-page-check { padding: 0 4px; }
.resource-content { min-height: 140px; }
.resource-card-shell { position: relative; min-width: 0; border-radius: var(--iot-radius-lg); transition: transform .18s, filter .18s; }
.resource-selector { position: absolute; z-index: 6; top: 16px; left: 16px; display: grid; width: 22px; height: 22px; margin: 0; padding: 0; place-items: center; border: 1px solid var(--iot-border-color-light); border-radius: 7px; background: color-mix(in srgb, var(--iot-bg-card) 96%, var(--iot-bg-page)); }
.resource-selector :deep(.el-checkbox__input) { display: inline-flex; }
.resource-selector :deep(.el-checkbox__label) { display: none; }
.resource-card-shell :deep(.sensor-card__header) { padding-left: 34px; }
.resource-card-shell.is-selected :deep(.sensor-card) { border-color: var(--iot-color-primary); background: linear-gradient(145deg, var(--iot-bg-card), var(--iot-color-primary-bg)); box-shadow: 0 0 0 2px var(--iot-color-primary-bg), var(--iot-shadow-md); }
.resource-card-shell.is-selected::after { content: ''; position: absolute; inset: 0; border: 1px solid color-mix(in srgb, var(--iot-color-primary) 42%, transparent); border-radius: var(--iot-radius-lg); pointer-events: none; }
.resource-card-shell.is-drag-bundle { filter: saturate(1.04); }
.drag-count-badge { position: absolute; z-index: 9; top: -9px; right: -7px; display: grid; min-width: 26px; height: 26px; padding: 0 7px; place-items: center; border: 2px solid var(--iot-bg-card); border-radius: 999px; color: white; background: var(--iot-color-primary); box-shadow: 0 5px 14px color-mix(in srgb, var(--iot-color-primary) 28%, transparent); font-size: 12px; font-weight: 700; }
.pagination-row { display: flex; justify-content: center; padding: 26px 0 8px; overflow-x: auto; }

.field-tag {
  margin: 2px 4px 2px 0;
  background-color: var(--iot-color-primary-bg) !important;
  border-color: transparent !important;
  color: var(--iot-color-primary-dark) !important;
}

html.dark .field-tag {
  color: var(--iot-color-primary-light) !important;
}

.commands-editor {
  width: 100%;
}

.cmd-error {
  color: var(--iot-color-danger);
  font-size: var(--iot-font-size-xs);
  margin-top: 4px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-md);
  padding: var(--iot-spacing-md) var(--iot-spacing-lg);
  flex-wrap: wrap;
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
  border-radius: var(--iot-border-radius-md, 8px);
}
.drag-chosen { transform: translateY(-2px); }
.drag-active { transform: rotate(.7deg) scale(1.015); filter: drop-shadow(0 14px 20px rgba(79,53,38,.16)); }
@media (max-width: 700px) {
  .resource-list-heading { align-items: flex-start; flex-direction: column; }
  .filter-bar :deep(.el-select), .filter-bar :deep(.el-input) { width: 100% !important; }
}
</style>
