<template>
  <section class="folder-browser iot-card iot-mb-lg" :class="{ 'is-dragging-resources': draggingCount > 0 }">
    <div class="folder-intro">
      <div class="folder-intro__icon"><FolderOpened /></div>
      <div>
        <h2>{{ ls.t('resourceFolders.directoryTitle') }}</h2>
        <p>{{ ls.t('resourceFolders.directorySubtitle') }}</p>
      </div>
    </div>
    <div class="folder-toolbar">
      <div class="folder-modes">
        <el-button :type="mode === 'folder' && !currentFolderId ? 'primary' : 'default'" @click="navigate('folder', null)">
          <el-icon><House /></el-icon>{{ ls.t('resourceFolders.directoryHome') }}
        </el-button>
        <el-button :type="mode === 'all' ? 'primary' : 'default'" @click="navigate('all', null)">{{ ls.t('resourceFolders.allResources') }}</el-button>
        <el-button :type="mode === 'unfiled' ? 'primary' : 'default'" @click="navigate('unfiled', null)">{{ ls.t('resourceFolders.unfiled') }}</el-button>
      </div>
      <el-button v-if="isStaff && mode === 'folder'" type="primary" plain :icon="FolderAdd" @click="openCreate">
        {{ ls.t('resourceFolders.newFolder') }}
      </el-button>
    </div>

    <transition name="drag-hint">
      <div v-if="draggingCount > 0" class="folder-drag-hint">
        <span class="folder-drag-hint__pulse" />
        {{ ls.t('resourceFolders.dragHint').replace('{count}', draggingCount) }}
      </div>
    </transition>

    <el-breadcrumb v-if="mode === 'folder'" separator="/" class="folder-breadcrumb">
      <el-breadcrumb-item>
        <button type="button" class="crumb-button" @click="navigate('folder', null)">{{ ls.t('resourceFolders.root') }}</button>
      </el-breadcrumb-item>
      <el-breadcrumb-item v-for="item in breadcrumbs" :key="item.id">
        <button type="button" class="crumb-button" @click="navigate('folder', item.id)">{{ item.name }}</button>
      </el-breadcrumb-item>
    </el-breadcrumb>

    <div v-if="visibleFolders.length" class="folder-grid">
      <article
        v-for="folder in visibleFolders"
        :key="folder.id"
        class="folder-card"
        :class="{
          'is-drop-ready': draggingCount > 0,
          'is-drop-active': dropTargetId === folder.id,
        }"
        @dragenter.prevent.stop="dropTargetId = folder.id"
        @dragover.prevent.stop="dropTargetId = folder.id"
        @dragleave="handleDragLeave"
        @drop.prevent.stop="handleDrop(folder.id)"
      >
        <button
          type="button"
          class="folder-card__main"
          :aria-label="`${folder.name}，${folder.resource_count} ${ls.t('resourceFolders.resourceUnit')}`"
          @click="navigate('folder', folder.id)"
        >
          <el-icon class="folder-icon"><FolderOpened v-if="dropTargetId === folder.id" /><Folder v-else /></el-icon>
          <span class="folder-meta">
            <strong>{{ folder.name }}</strong>
            <span>{{ folder.resource_count }} {{ ls.t('resourceFolders.resourceUnit') }} · {{ folder.child_count }} {{ ls.t('resourceFolders.childUnit') }}</span>
            <em v-if="dropTargetId === folder.id">{{ ls.t('resourceFolders.dropHere') }}</em>
          </span>
        </button>
        <el-dropdown v-if="isStaff" trigger="click" @command="(command) => onFolderCommand(command, folder)" @click.stop>
          <el-button text circle :icon="MoreFilled" @click.stop />
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="rename">{{ ls.t('resourceFolders.rename') }}</el-dropdown-item>
              <el-dropdown-item command="move">{{ ls.t('resourceFolders.move') }}</el-dropdown-item>
              <el-dropdown-item command="up">{{ ls.t('resourceFolders.moveUp') }}</el-dropdown-item>
              <el-dropdown-item command="down">{{ ls.t('resourceFolders.moveDown') }}</el-dropdown-item>
              <el-dropdown-item command="delete" divided>{{ ls.t('common.delete') }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </article>
    </div>

    <el-dialog v-model="editVisible" :title="editingFolder ? ls.t('resourceFolders.editFolder') : ls.t('resourceFolders.newFolder')" width="460px" destroy-on-close>
      <el-form label-width="90px" @submit.prevent>
        <el-form-item :label="ls.t('resourceFolders.name')" required>
          <el-input v-model="folderForm.name" maxlength="100" show-word-limit @keyup.enter="saveFolder" />
        </el-form-item>
        <el-form-item :label="ls.t('resourceFolders.parent')">
          <el-select v-model="folderForm.parent" clearable :placeholder="ls.t('resourceFolders.root')" style="width: 100%">
            <el-option v-for="option in parentOptions" :key="option.id" :label="option.label" :value="option.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">{{ ls.t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="saveFolder">{{ ls.t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Folder, FolderAdd, FolderOpened, House, MoreFilled } from '@element-plus/icons-vue'
import { useLocaleStore } from '@/stores/locale'
import {
  createResourceFolder,
  deleteResourceFolder,
  getResourceFolders,
  reorderResourceFolders,
  updateResourceFolder,
} from '@/api/resourceFolders'

const props = defineProps({
  resourceType: { type: String, required: true },
  mode: { type: String, default: 'folder' },
  currentFolderId: { type: Number, default: null },
  isStaff: { type: Boolean, default: false },
  draggingCount: { type: Number, default: 0 },
})
const emit = defineEmits(['navigate', 'loaded', 'drop-resources'])
const ls = useLocaleStore()

const folders = ref([])
const editVisible = ref(false)
const editingFolder = ref(null)
const saving = ref(false)
const folderForm = ref({ name: '', parent: null })
const dropTargetId = ref(null)

const folderMap = computed(() => new Map(folders.value.map((item) => [item.id, item])))
const visibleFolders = computed(() => {
  const parentId = props.mode === 'folder' ? (props.currentFolderId ?? null) : null
  return folders.value.filter((item) => (item.parent ?? null) === parentId)
})
const breadcrumbs = computed(() => {
  const result = []
  let current = props.currentFolderId ? folderMap.value.get(props.currentFolderId) : null
  while (current) {
    result.unshift(current)
    current = current.parent ? folderMap.value.get(current.parent) : null
  }
  return result
})

function descendantsOf(folderId) {
  const result = new Set([folderId])
  let changed = true
  while (changed) {
    changed = false
    folders.value.forEach((item) => {
      if (item.parent && result.has(item.parent) && !result.has(item.id)) {
        result.add(item.id)
        changed = true
      }
    })
  }
  return result
}

function folderPath(folder) {
  const names = [folder.name]
  let parent = folder.parent ? folderMap.value.get(folder.parent) : null
  while (parent) {
    names.unshift(parent.name)
    parent = parent.parent ? folderMap.value.get(parent.parent) : null
  }
  return names.join(' / ')
}

const parentOptions = computed(() => {
  const excluded = editingFolder.value ? descendantsOf(editingFolder.value.id) : new Set()
  return folders.value
    .filter((item) => !excluded.has(item.id))
    .map((item) => ({ ...item, label: folderPath(item) }))
})

async function loadFolders() {
  try {
    const data = await getResourceFolders(props.resourceType)
    folders.value = data.results || data
    emit('loaded', folders.value)
  } catch {
    ElMessage.error(ls.t('resourceFolders.loadFailed'))
  }
}

function navigate(mode, folderId) {
  emit('navigate', { mode, folderId })
}

function handleDragLeave(event) {
  if (!event.currentTarget.contains(event.relatedTarget)) dropTargetId.value = null
}

function handleDrop(folderId) {
  if (!props.draggingCount) return
  dropTargetId.value = null
  emit('drop-resources', { folderId })
}

function openCreate() {
  editingFolder.value = null
  folderForm.value = { name: '', parent: props.currentFolderId ?? null }
  editVisible.value = true
}

function onFolderCommand(command, folder) {
  if (command === 'up' || command === 'down') {
    changeFolderOrder(folder, command === 'up' ? -1 : 1)
    return
  }
  if (command === 'delete') {
    removeFolder(folder)
    return
  }
  editingFolder.value = folder
  folderForm.value = {
    name: folder.name,
    parent: command === 'move' ? (folder.parent ?? null) : (folder.parent ?? null),
  }
  editVisible.value = true
}

async function changeFolderOrder(folder, delta) {
  const ordered = [...visibleFolders.value]
  const index = ordered.findIndex((item) => item.id === folder.id)
  const target = index + delta
  if (index < 0 || target < 0 || target >= ordered.length) return
  ;[ordered[index], ordered[target]] = [ordered[target], ordered[index]]
  try {
    await reorderResourceFolders(ordered.map((item) => item.id))
    await loadFolders()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || ls.t('resourceFolders.saveFailed'))
  }
}

async function saveFolder() {
  const name = folderForm.value.name.trim()
  if (!name) {
    ElMessage.warning(ls.t('resourceFolders.folderNameRequired'))
    return
  }
  saving.value = true
  try {
    const payload = { name, parent: folderForm.value.parent ?? null }
    if (editingFolder.value) {
      await updateResourceFolder(editingFolder.value.id, payload)
      ElMessage.success(ls.t('resourceFolders.updated'))
    } else {
      await createResourceFolder({ ...payload, resource_type: props.resourceType })
      ElMessage.success(ls.t('resourceFolders.created'))
    }
    editVisible.value = false
    await loadFolders()
  } catch (error) {
    const data = error.response?.data
    ElMessage.error(data?.detail || data?.name?.[0] || data?.parent?.[0] || ls.t('resourceFolders.saveFailed'))
  } finally {
    saving.value = false
  }
}

async function removeFolder(folder) {
  try {
    await ElMessageBox.confirm(
      ls.t('resourceFolders.deleteConfirm').replace('{name}', folder.name),
      ls.t('resourceFolders.deleteTitle'), { type: 'warning' },
    )
    await deleteResourceFolder(folder.id)
    ElMessage.success(ls.t('resourceFolders.deleted'))
    await loadFolders()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error.response?.data?.detail || ls.t('resourceFolders.deleteFailed'))
  }
}

defineExpose({ refresh: loadFolders })
onMounted(loadFolders)
</script>

<style scoped>
.folder-browser {
  position: relative;
  overflow: hidden;
  padding: var(--iot-spacing-lg);
  border: 1px solid var(--iot-border-color-light);
  background:
    radial-gradient(circle at 92% 0, color-mix(in srgb, var(--iot-color-primary) 9%, transparent), transparent 32%),
    var(--iot-material-regular);
  box-shadow: var(--iot-shadow-sm);
  backdrop-filter: blur(var(--iot-material-blur)) saturate(145%);
  -webkit-backdrop-filter: blur(var(--iot-material-blur)) saturate(145%);
  transition:
    border-color var(--iot-transition-fast),
    box-shadow var(--iot-transition-fast),
    background-color var(--iot-transition-fast);
}

.folder-browser::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
  pointer-events: none;
}

.folder-browser.is-dragging-resources {
  border-color: color-mix(in srgb, var(--iot-color-primary) 52%, transparent);
  box-shadow: var(--iot-focus-ring), var(--iot-shadow-md);
}

.folder-intro {
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-sm);
  margin-bottom: var(--iot-spacing-md);
}

.folder-intro__icon {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--iot-color-primary) 16%, transparent);
  border-radius: var(--iot-radius-base);
  color: var(--iot-color-primary-dark);
  background: var(--iot-color-primary-bg);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
  font-size: 22px;
}

.folder-intro h2 {
  margin: 0;
  color: var(--iot-text-primary);
  font-family: var(--iot-font-display);
  font-size: var(--iot-font-size-md);
  letter-spacing: -0.012em;
}

.folder-intro p {
  margin: var(--iot-spacing-2xs) 0 0;
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-sm);
}

.folder-toolbar {
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  gap: var(--iot-spacing-sm);
  flex-wrap: wrap;
}

.folder-modes {
  display: flex;
  gap: var(--iot-spacing-2xs);
  padding: var(--iot-spacing-2xs);
  border: 1px solid var(--iot-separator);
  border-radius: var(--iot-radius-base);
  background: var(--iot-material-thick);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-xs);
  flex-wrap: wrap;
}

.folder-modes :deep(.el-button) {
  min-height: 38px;
  margin: 0;
  border: 0;
  border-radius: var(--iot-radius-sm);
  box-shadow: none;
  touch-action: manipulation;
}

.folder-modes :deep(.el-button:active:not(.is-disabled)) {
  transform: scale(0.97);
  transition-duration: 100ms;
}

.folder-modes :deep(.el-button:focus-visible),
.folder-card :deep(.el-button:focus-visible) {
  outline: none;
  box-shadow: var(--iot-focus-ring) !important;
}

.folder-breadcrumb {
  margin-top: var(--iot-spacing-md);
  padding: var(--iot-spacing-sm) var(--iot-spacing-md);
  border: 1px solid var(--iot-separator);
  border-radius: var(--iot-radius-base);
  background: var(--iot-material-thick);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-xs);
}

.crumb-button {
  border: 0;
  border-radius: var(--iot-radius-xs);
  padding: 2px 4px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
  touch-action: manipulation;
  transition:
    color var(--iot-transition-fast),
    background-color var(--iot-transition-fast),
    transform var(--iot-transition-instant);
}

.crumb-button:hover {
  color: var(--iot-color-primary);
  background: var(--iot-color-primary-bg);
}

.crumb-button:active {
  transform: scale(0.96);
}

.crumb-button:focus-visible {
  outline: none;
  box-shadow: var(--iot-focus-ring);
}

.folder-drag-hint {
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-xs);
  margin-top: var(--iot-spacing-sm);
  padding: var(--iot-spacing-sm) var(--iot-spacing-md);
  border: 1px dashed color-mix(in srgb, var(--iot-color-primary) 58%, transparent);
  border-radius: var(--iot-radius-base);
  color: var(--iot-color-primary-dark);
  background: color-mix(in srgb, var(--iot-bg-card-solid) 88%, var(--iot-color-primary));
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
  font-size: var(--iot-font-size-sm);
}

.folder-drag-hint__pulse {
  width: 8px;
  height: 8px;
  border-radius: var(--iot-radius-round);
  background: var(--iot-color-primary);
  box-shadow: 0 0 0 5px color-mix(in srgb, var(--iot-color-primary) 14%, transparent);
  animation: drop-pulse 1.2s var(--iot-ease-standard) infinite;
}

.folder-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(225px, 1fr));
  gap: var(--iot-spacing-sm);
  margin-top: var(--iot-spacing-md);
}

.folder-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-sm);
  min-height: 84px;
  padding: var(--iot-spacing-md);
  overflow: hidden;
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-lg);
  background: var(--iot-bg-card-solid);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-sm);
  cursor: pointer;
  touch-action: manipulation;
  transition:
    border-color var(--iot-transition-fast),
    transform var(--iot-transition-fast),
    box-shadow var(--iot-transition-fast),
    background-color var(--iot-transition-fast);
}

.folder-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: var(--iot-spacing-md);
  width: 46px;
  height: 4px;
  border-radius: 0 0 var(--iot-radius-xs) var(--iot-radius-xs);
  background: color-mix(in srgb, var(--iot-color-primary) 42%, transparent);
}

.folder-card__main {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: var(--iot-spacing-sm);
  padding: 0;
  border: 0;
  border-radius: var(--iot-radius-base);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.folder-card__main:focus-visible {
  box-shadow: none;
}

@media (hover: hover) and (pointer: fine) {
  .folder-card:hover {
    border-color: color-mix(in srgb, var(--iot-color-primary) 48%, transparent);
    background: var(--iot-bg-card-hover);
    box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-base);
    transform: translateY(-2px);
  }
}

.folder-card:active {
  transform: translateY(0) scale(0.985);
  transition-duration: 100ms;
}

.folder-card:focus-within {
  border-color: var(--iot-color-primary);
  box-shadow: var(--iot-focus-ring), var(--iot-shadow-base);
}

.folder-card.is-drop-ready {
  border-style: dashed;
}

.folder-card.is-drop-active {
  border-color: var(--iot-color-primary);
  border-style: solid;
  background: color-mix(in srgb, var(--iot-bg-card-solid) 78%, var(--iot-color-primary));
  box-shadow: var(--iot-focus-ring), var(--iot-shadow-md);
  transform: translateY(-3px) scale(1.01);
}

.folder-icon {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--iot-color-primary) 14%, transparent);
  border-radius: var(--iot-radius-base);
  color: var(--iot-color-primary);
  background: var(--iot-color-primary-bg);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
  font-size: 25px;
}

.is-drop-active .folder-icon {
  border-color: var(--iot-color-primary);
  color: var(--iot-text-inverse);
  background: var(--iot-color-primary);
  box-shadow: none;
}

.folder-meta {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: var(--iot-spacing-2xs);
}

.folder-meta strong {
  overflow: hidden;
  color: var(--iot-text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.folder-meta span {
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-xs);
}

.folder-meta em {
  color: var(--iot-color-primary-dark);
  font-size: var(--iot-font-size-xs);
  font-style: normal;
  font-weight: 600;
}

.drag-hint-enter-active,
.drag-hint-leave-active {
  transition:
    opacity var(--iot-transition-fast),
    transform var(--iot-transition-fast);
}

.drag-hint-enter-from,
.drag-hint-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}

@keyframes drop-pulse {
  50% {
    box-shadow: 0 0 0 8px transparent;
  }
}

@media (prefers-reduced-motion: reduce) {
  .folder-browser,
  .folder-card,
  .crumb-button,
  .folder-modes :deep(.el-button) {
    transition-duration: 1ms !important;
  }

  .folder-card:hover,
  .folder-card:active,
  .folder-card.is-drop-active,
  .crumb-button:active,
  .folder-modes :deep(.el-button:active:not(.is-disabled)) {
    transform: none;
  }

  .folder-drag-hint__pulse {
    animation: none;
  }

  .drag-hint-enter-active,
  .drag-hint-leave-active {
    transition: opacity 120ms ease-out;
  }

  .drag-hint-enter-from,
  .drag-hint-leave-to {
    transform: none;
  }
}

@media (prefers-reduced-transparency: reduce) {
  .folder-browser {
    background: var(--iot-bg-card-solid);
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .folder-modes,
  .folder-breadcrumb,
  .folder-card {
    background: var(--iot-bg-card-solid);
  }

  .folder-browser::after {
    box-shadow: none;
  }
}

@media (prefers-contrast: more) {
  .folder-browser,
  .folder-modes,
  .folder-breadcrumb,
  .folder-card {
    border-color: var(--iot-border-color);
  }
}

@media (max-width: 640px) {
  .folder-browser {
    padding: var(--iot-spacing-md);
  }

  .folder-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .folder-modes {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }

  .folder-modes :deep(.el-button) {
    min-height: 44px;
  }

  .folder-modes :deep(.el-button:first-child) {
    grid-column: 1 / -1;
  }

  .folder-grid {
    grid-template-columns: 1fr;
  }
}
</style>
