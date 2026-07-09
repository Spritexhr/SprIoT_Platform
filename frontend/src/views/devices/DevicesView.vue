<template>
  <div class="devices-view">
    <div class="iot-page-header">
      <div>
        <h1 class="iot-page-title">{{ ls.t('devices.title') }}</h1>
        <p class="iot-page-subtitle">{{ ls.t('devices.subtitle') }}</p>
      </div>
    </div>

    <!-- ==================== 设备类型管理 ==================== -->
    <details class="iot-card iot-mb-lg type-details">
      <summary class="type-summary">{{ ls.t('devices.typeManagement') }}<span>{{ ls.t('resourceFolders.expandConfig') }}</span></summary>
      <div class="iot-card__header">
        <span class="section-title">{{ ls.t('devices.typeManagement') }}</span>
        <el-button v-if="isStaff" type="primary" :icon="Plus" @click="openDeviceTypeDialog(null)">
          {{ ls.t('devices.addType') }}
        </el-button>
      </div>
      <div class="iot-card__body">
        <el-table :data="deviceTypes" v-loading="deviceTypesLoading" stripe>
          <el-table-column prop="DeviceType_id" :label="ls.t('common.typeId')" width="160" />
          <el-table-column prop="name" :label="ls.t('common.name')" width="180" />
          <el-table-column :label="ls.t('devices.configParams')" min-width="200">
            <template #default="{ row }">
              <el-tag
                v-for="field in (row.config_parameters || [])"
                :key="field"
                size="small"
                class="field-tag"
              >
                {{ field }}
              </el-tag>
              <span v-if="!row.config_parameters || row.config_parameters.length === 0" class="iot-text-secondary">-</span>
            </template>
          </el-table-column>
          <el-table-column :label="ls.t('common.cmdCount')" width="80" align="center">
            <template #default="{ row }">
              {{ row.commands ? Object.keys(row.commands).length : 0 }}
            </template>
          </el-table-column>
          <el-table-column :label="ls.t('devices.relatedDevices')" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ row.device_count || 0 }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="isStaff" :label="ls.t('common.operations')" width="160" align="center" fixed="right">
            <template #default="{ row }">
              <el-button text type="primary" size="small" @click="openDeviceTypeDialog(row)">
                {{ ls.t('common.edit') }}
              </el-button>
              <el-popconfirm
                :title="ls.t('devices.deleteTypeConfirm')"
                :confirm-button-text="ls.t('common.delete')"
                :cancel-button-text="ls.t('common.cancel')"
                @confirm="handleDeleteDeviceType(row.id)"
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
      resource-type="device"
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
        <el-select v-model="filterType" :placeholder="ls.t('devices.filterType')" style="width: 200px" clearable @change="handleFilterChange">
          <el-option :label="ls.t('common.allTypes')" value="" />
          <el-option
            v-for="t in deviceTypes"
            :key="t.id"
            :label="t.name"
            :value="t.id"
          />
        </el-select>
        <el-select v-model="filterOnline" :placeholder="ls.t('devices.filterStatus')" style="width: 140px" clearable @change="handleFilterChange">
          <el-option :label="ls.t('common.all')" value="" />
          <el-option :label="ls.t('common.online')" value="true" />
          <el-option :label="ls.t('common.offline')" value="false" />
        </el-select>
        <el-input
          v-model="searchText"
          :placeholder="ls.t('devices.searchPlaceholder')"
          style="width: 260px"
          clearable
          @clear="handleFilterChange"
          @keyup.enter="handleFilterChange"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button :icon="Refresh" circle @click="fetchDevices" />
        <el-checkbox v-if="isStaff && devices.length" class="select-page-check" :model-value="allPageSelected" @change="toggleSelectPage">
          {{ ls.t('resourceFolders.selectCurrentPage') }}
        </el-checkbox>
        <el-button v-if="isStaff && selectedIds.length" class="selected-move-button" @click="moveDialogVisible = true">
          {{ ls.t('resourceFolders.moveSelected') }}（{{ selectedIds.length }}）
        </el-button>
        <el-button v-if="isStaff && selectedIds.length" type="danger" plain :icon="Delete" @click="handleBulkDeleteDevices">
          删除选中（{{ selectedIds.length }}）
        </el-button>
        <el-button v-if="isStaff && devices.length" type="primary" :icon="Plus" @click="openAddDialog">{{ ls.t('devices.addDevice') }}</el-button>
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

    <!-- 卡片网格 / 空状态 -->
    <div v-loading="loading" class="resource-content">
      <draggable
        v-if="devices.length"
        v-model="devices"
        :item-key="'device_id'"
        :disabled="!isStaff"
        :sort="canReorder"
        :animation="200"
        ghost-class="drag-ghost"
        chosen-class="drag-chosen"
        drag-class="drag-active"
        handle=".device-drag-handle, .iot-card"
        class="iot-grid iot-grid--cards"
        @start="handleDragStart"
        @end="handleReorderEnd"
      >
        <template #item="{ element: d }">
          <div
            class="resource-card-shell"
            :class="{
              'is-selected': selectedIds.includes(d.device_id),
              'is-drag-bundle': draggingIds.includes(d.device_id),
            }"
            :data-resource-id="d.device_id"
          >
            <el-checkbox
              v-if="isStaff"
              class="resource-selector"
              :model-value="selectedIds.includes(d.device_id)"
              @click.stop
              @change="(checked) => toggleSelection(d.device_id, checked)"
            />
            <span v-if="dragAnchorId === d.device_id && draggingIds.length > 1" class="drag-count-badge">
              {{ draggingIds.length }}
            </span>
            <DeviceCard :device="d" :show-delete="false" @click="goDetail(d)" />
          </div>
        </template>
      </draggable>
      <div v-if="!devices.length" class="iot-card empty-card">
        <el-empty :description="loading ? ls.t('common.loading') : ls.t('devices.noDevices')">
          <el-button v-if="isStaff" type="primary" :icon="Plus" @click="openAddDialog">{{ ls.t('devices.addDevice') }}</el-button>
        </el-empty>
      </div>
      <div v-if="total > 0" class="pagination-row">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[24, 48, 96]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          @current-change="fetchDevices"
          @size-change="handlePageSizeChange"
        />
      </div>
    </div>

    <!-- 设备类型编辑弹窗 -->
    <el-dialog
      v-model="deviceTypeDialogVisible"
      :title="deviceTypeForm.id ? ls.t('devices.editTypeTitle') : ls.t('devices.addTypeTitle')"
      width="600px"
      destroy-on-close
    >
      <el-form :model="deviceTypeForm" label-width="100px" :rules="deviceTypeRules" ref="deviceTypeFormRef">
        <el-form-item :label="ls.t('devices.typeIdLabel')" prop="DeviceType_id">
          <el-input v-model="deviceTypeForm.DeviceType_id" :placeholder="ls.t('devices.typeIdPlaceholder')" />
        </el-form-item>
        <el-form-item :label="ls.t('common.name')" prop="name">
          <el-input v-model="deviceTypeForm.name" :placeholder="ls.t('devices.namePlaceholder')" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="deviceTypeForm.description" type="textarea" :rows="2" :placeholder="ls.t('devices.descPlaceholder')" />
        </el-form-item>
        <el-form-item :label="ls.t('devices.configParamsLabel')">
          <el-select
            v-model="deviceTypeForm.config_parameters"
            multiple
            filterable
            allow-create
            default-first-option
            :placeholder="ls.t('devices.configParamsPlaceholder')"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item :label="ls.t('devices.commandsLabel')">
          <div class="commands-editor">
            <el-input
              v-model="deviceTypeForm.commands_json"
              type="textarea"
              :rows="6"
              :placeholder="ls.t('devices.commandsPlaceholder')"
            />
            <div v-if="deviceTypeCmdError" class="cmd-error">{{ deviceTypeCmdError }}</div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deviceTypeDialogVisible = false">{{ ls.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="deviceTypeSaving" @click="handleSaveDeviceType">
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 添加设备弹窗 -->
    <el-dialog v-model="addDialogVisible" :title="ls.t('devices.addDialogTitle')" width="500px" destroy-on-close>
      <el-form :model="addForm" label-width="100px" :rules="addRules" ref="addFormRef">
        <el-form-item :label="ls.t('devices.deviceIdLabel')" prop="device_id">
          <el-input v-model="addForm.device_id" :placeholder="ls.t('devices.deviceIdPlaceholder')" />
        </el-form-item>
        <el-form-item :label="ls.t('common.name')" prop="name">
          <el-input v-model="addForm.name" :placeholder="ls.t('devices.deviceNamePlaceholder')" />
        </el-form-item>
        <el-form-item label="设备类型" prop="device_type">
          <el-select v-model="addForm.device_type" placeholder="选择类型" style="width: 100%">
            <el-option
              v-for="t in deviceTypes"
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
          <el-input v-model="addForm.location" :placeholder="ls.t('devices.locationPlaceholder')" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="addForm.description" type="textarea" :rows="2" :placeholder="ls.t('common.optional')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialogVisible = false">{{ ls.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="addSaving" @click="handleAddDevice">保存</el-button>
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
import { getDevices, createDevice, deleteDevice, getDeviceTypes, createDeviceType, updateDeviceType, deleteDeviceType, reorderDevices, bulkMoveDevices } from '@/api/devices'
import draggable from 'vuedraggable'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import ResourceFolderBrowser from '@/components/resources/ResourceFolderBrowser.vue'
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
const devices = ref([])
const deviceTypes = ref([])
const loading = ref(false)
const deviceTypesLoading = ref(false)
const allPageSelected = computed(() => devices.value.length > 0 && devices.value.every((item) => selectedIds.value.includes(item.device_id)))

async function fetchDevices() {
  loading.value = true
  try {
    const params = {}
    params.page = page.value
    params.page_size = pageSize.value
    if (filterType.value) params.device_type = filterType.value
    if (filterOnline.value) params.online = filterOnline.value
    if (searchText.value) params.search = searchText.value
    if (browseMode.value === 'unfiled' || (browseMode.value === 'folder' && !currentFolderId.value)) {
      params.folder = 'unfiled'
    } else if (browseMode.value === 'folder' && currentFolderId.value) {
      params.folder = currentFolderId.value
    }
    const data = await getDevices(params)
    devices.value = data.results || data
    total.value = data.count ?? devices.value.length
    selectedIds.value = selectedIds.value.filter((id) => devices.value.some((item) => item.device_id === id))
  } catch {
    ElMessage.error(ls.t('devices.fetchListFailed'))
  } finally {
    loading.value = false
  }
}

function handleFilterChange() {
  page.value = 1
  selectedIds.value = []
  fetchDevices()
}

function handlePageSizeChange() {
  page.value = 1
  selectedIds.value = []
  fetchDevices()
}

function handleFolderNavigate({ mode, folderId }) {
  browseMode.value = mode
  currentFolderId.value = folderId ?? null
  page.value = 1
  selectedIds.value = []
  fetchDevices()
}

function handleFoldersLoaded(items) {
  folders.value = items
}

function toggleSelection(deviceId, checked) {
  if (checked && !selectedIds.value.includes(deviceId)) selectedIds.value.push(deviceId)
  if (!checked) selectedIds.value = selectedIds.value.filter((id) => id !== deviceId)
}

function toggleSelectPage(checked) {
  selectedIds.value = checked ? devices.value.map((item) => item.device_id) : []
}

async function handleBulkMove() {
  await moveDevicesToFolder([...selectedIds.value], moveTargetFolder.value ?? null, true)
}

async function moveDevicesToFolder(ids, folderId, closeDialog = false) {
  if (!ids.length) return
  moveSaving.value = true
  try {
    await bulkMoveDevices(ids, folderId)
    ElMessage.success(ls.t('resourceFolders.moved'))
    if (closeDialog) moveDialogVisible.value = false
    selectedIds.value = []
    await fetchDevices()
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
    await moveDevicesToFolder(ids, folderId)
  } finally {
    draggingIds.value = []
    dragAnchorId.value = null
    folderDropActive.value = false
  }
}

async function fetchDeviceTypeList() {
  deviceTypesLoading.value = true
  try {
    const data = await getDeviceTypes()
    deviceTypes.value = data.results || data
  } catch {
    ElMessage.error(ls.t('devices.fetchTypesFailed'))
  } finally {
    deviceTypesLoading.value = false
  }
}

// ==================== 设备类型管理 ====================
const deviceTypeDialogVisible = ref(false)
const deviceTypeSaving = ref(false)
const deviceTypeFormRef = ref(null)
const deviceTypeCmdError = ref('')

const deviceTypeRules = computed(() => ({
  DeviceType_id: [{ required: true, message: ls.t('devices.typeIdRequired'), trigger: 'blur' }],
  name: [{ required: true, message: ls.t('devices.nameRequired'), trigger: 'blur' }],
}))

const emptyDeviceTypeForm = () => ({
  id: null,
  DeviceType_id: '',
  name: '',
  description: '',
  config_parameters: [],
  commands_json: '{}',
})

const deviceTypeForm = ref(emptyDeviceTypeForm())

function openDeviceTypeDialog(row) {
  deviceTypeCmdError.value = ''
  if (row) {
    deviceTypeForm.value = {
      id: row.id,
      DeviceType_id: row.DeviceType_id,
      name: row.name,
      description: row.description || '',
      config_parameters: row.config_parameters || [],
      commands_json: JSON.stringify(row.commands || {}, null, 2),
    }
  } else {
    deviceTypeForm.value = emptyDeviceTypeForm()
  }
  deviceTypeDialogVisible.value = true
}

async function handleSaveDeviceType() {
  const formEl = deviceTypeFormRef.value
  if (formEl) {
    const valid = await formEl.validate().catch(() => false)
    if (!valid) return
  }

  let commands
  try {
    commands = JSON.parse(deviceTypeForm.value.commands_json)
    deviceTypeCmdError.value = ''
  } catch {
    deviceTypeCmdError.value = ls.t('common.jsonError')
    return
  }

  const payload = {
    DeviceType_id: deviceTypeForm.value.DeviceType_id,
    name: deviceTypeForm.value.name,
    description: deviceTypeForm.value.description,
    config_parameters: deviceTypeForm.value.config_parameters,
    commands: commands,
  }

  deviceTypeSaving.value = true
  try {
    if (deviceTypeForm.value.id) {
      await updateDeviceType(deviceTypeForm.value.id, payload)
      ElMessage.success(ls.t('devices.typeUpdated'))
    } else {
      await createDeviceType(payload)
      ElMessage.success(ls.t('devices.typeCreated'))
    }
    deviceTypeDialogVisible.value = false
    fetchDeviceTypeList()
  } catch (err) {
    const detail = err.response?.data
    const msg = typeof detail === 'object' ? Object.values(detail).flat().join('；') : ls.t('common.saveFailed')
    ElMessage.error(msg)
  } finally {
    deviceTypeSaving.value = false
  }
}

async function handleDeleteDeviceType(id) {
  try {
    await deleteDeviceType(id)
    ElMessage.success(ls.t('common.deleteSuccess'))
    fetchDeviceTypeList()
  } catch {
    ElMessage.error(ls.t('devices.deleteTypeFailed'))
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
  const device = devices.value[evt.oldIndex]
  if (!device) return
  dragAnchorId.value = device.device_id
  draggingIds.value = selectedIds.value.includes(device.device_id)
    ? [...selectedIds.value]
    : [device.device_id]
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
  const order = devices.value.map(d => d.device_id)
  try {
    await reorderDevices(order, {
      page: page.value,
      page_size: pageSize.value,
      folder: currentFolderId.value ?? 'unfiled',
    })
  } catch {
    ElMessage.error(ls.t('devices.reorderFailed') || '排序保存失败')
    fetchDevices()
  }
}

// ==================== 跳转详情 ====================
function goDetail(device) {
  router.push({ name: 'DeviceDetail', params: { deviceId: device.device_id } })
}

// ==================== 删除设备 ====================
async function handleDeleteDevice(device) {
  try {
    await ElMessageBox.confirm(
      ls.t('devices.deleteConfirmMsg').replace('{name}', device.name),
      ls.t('common.deleteConfirmTitle'),
      { confirmButtonText: ls.t('common.deleteConfirmOk'), cancelButtonText: ls.t('common.cancel'), type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await deleteDevice(device.device_id)
    ElMessage.success(ls.t('devices.deviceDeleted'))
    fetchDevices()
    folderBrowserRef.value?.refresh()
  } catch {
    ElMessage.error(ls.t('common.deleteFailed'))
  }
}

async function handleBulkDeleteDevices() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确认删除已选中的 ${ids.length} 个设备对象？此操作不可恢复。`,
      ls.t('common.deleteConfirmTitle'),
      { confirmButtonText: ls.t('common.deleteConfirmOk'), cancelButtonText: ls.t('common.cancel'), type: 'warning' }
    )
  } catch {
    return
  }
  loading.value = true
  try {
    await Promise.all(ids.map((deviceId) => deleteDevice(deviceId)))
    ElMessage.success(`已删除 ${ids.length} 个设备`)
    selectedIds.value = []
    await fetchDevices()
    await folderBrowserRef.value?.refresh()
  } catch {
    ElMessage.error(ls.t('common.deleteFailed'))
  } finally {
    loading.value = false
  }
}

// ==================== 添加设备 ====================
const addDialogVisible = ref(false)
const addSaving = ref(false)
const addFormRef = ref(null)
const addForm = ref({
  device_id: '',
  name: '',
  device_type: null,
  location: '',
  description: '',
  folder: null,
})

const addRules = computed(() => ({
  device_id: [{ required: true, message: ls.t('devices.deviceIdRequired'), trigger: 'blur' }],
  name: [{ required: true, message: ls.t('devices.deviceNameRequired'), trigger: 'blur' }],
  device_type: [{ required: true, message: ls.t('devices.deviceTypeRequired'), trigger: 'change' }],
}))

function openAddDialog() {
  const defaultFolder = browseMode.value === 'folder' ? currentFolderId.value : null
  addForm.value = { device_id: '', name: '', device_type: null, location: '', description: '', folder: defaultFolder }
  addDialogVisible.value = true
}

async function handleAddDevice() {
  const formEl = addFormRef.value
  if (formEl) {
    const valid = await formEl.validate().catch(() => false)
    if (!valid) return
  }
  addSaving.value = true
  try {
    await createDevice(addForm.value)
    ElMessage.success(ls.t('devices.deviceAdded'))
    addDialogVisible.value = false
    fetchDevices()
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
function onDeviceStatus(data) {
  if (!data || !data.device_id) return
  const row = devices.value.find(d => d.device_id === data.device_id)
  if (!row) return
  const tsIso = data.timestamp ? new Date(data.timestamp * 1000).toISOString() : null
  row.latest_data = {
    data: data.status,
    timestamp: tsIso,
    received_at: data.received_at ? new Date(data.received_at * 1000).toISOString() : tsIso,
  }
  row.is_online = !!data.is_online
  if (data.last_seen) row.last_seen = new Date(data.last_seen * 1000).toISOString()
}

useWebSocket(
  () => buildWsUrl('/ws/devices/'),
  { 'device.status': onDeviceStatus },
)

// ==================== 初始化 ====================
onMounted(() => {
  fetchDevices()
  fetchDeviceTypeList()
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
.resource-card-shell :deep(.device-card__header) { padding-left: 34px; }
.resource-card-shell.is-selected :deep(.device-card) { border-color: var(--iot-color-primary); background: linear-gradient(145deg, var(--iot-bg-card), var(--iot-color-primary-bg)); box-shadow: 0 0 0 2px var(--iot-color-primary-bg), var(--iot-shadow-md); }
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
