<template>
  <div ref="containerRef" class="dv">
    <div class="dv__bar">
      <span class="dv__name">{{ view.name }}</span>
      <div class="dv__actions">
        <el-tag v-if="dirty" type="warning" size="small" effect="plain">未保存</el-tag>
        <el-button v-if="canEdit" size="small" @click="toggleMode">
          {{ mode === 'edit' ? '预览模式' : '编辑模式' }}
        </el-button>
        <el-button v-if="mode === 'edit' && canEdit" type="primary" size="small"
                   :loading="saving" :disabled="!dirty" @click="save">
          保存
        </el-button>
        <el-button size="small" @click="toggleFullscreen" :title="isFullscreen ? '退出全屏' : '全屏'">
          <svg v-if="!isFullscreen" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
            <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
          </svg>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/>
            <line x1="10" y1="14" x2="3" y2="21"/><line x1="21" y1="3" x2="14" y2="10"/>
          </svg>
        </el-button>
      </div>
    </div>

    <DiagramEditor
      v-if="mode === 'edit'"
      :diagram="diagramObj"
      :targets="targets"
      @change="onCanvasChange"
    />
    <DiagramRuntime v-else :diagram="diagramObj" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave } from 'vue-router'

import DiagramEditor from '../diagram/editor/DiagramEditor.vue'
import DiagramRuntime from '../diagram/runtime/DiagramRuntime.vue'
import { useProjectStore } from '@/stores/project'
import { updateView } from '@/api/projects'
import { getAutomationRules } from '@/api/automation'
import { listControlSchemes } from '@/api/controlSchemes'
import { emptyCanvas } from '../diagram/canvasCodec'

const props = defineProps({
  // ProjectView 对象：{ id, name, view_type:'diagram', config(canvas) }
  view: { type: Object, required: true },
  canEdit: { type: Boolean, default: false },
})
const emit = defineEmits(['saved'])

const store = useProjectStore()

// ============ 全屏 ============
const containerRef = ref(null)
const isFullscreen = ref(false)

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    containerRef.value?.requestFullscreen()
  } else {
    document.exitFullscreen()
  }
}

function onFullscreenChange() {
  isFullscreen.value = !!document.fullscreenElement
}

function onBeforeUnload(event) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  document.addEventListener('fullscreenchange', onFullscreenChange)
  window.addEventListener('beforeunload', onBeforeUnload)
})
onUnmounted(() => {
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  window.removeEventListener('beforeunload', onBeforeUnload)
})

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('当前 P&ID 画布尚未保存，离开后修改将丢失。', '未保存修改', {
      confirmButtonText: '放弃修改并离开',
      cancelButtonText: '继续编辑',
      type: 'warning',
      distinguishCancelAndClose: true,
    })
    return true
  } catch {
    return false
  }
})

const canvas = ref(
  props.view.config && Array.isArray(props.view.config.nodes) ? props.view.config : emptyCanvas(),
)

const mode = ref('view')
const dirty = ref(false)
const saving = ref(false)
const automationRules = ref([])
const controlSchemes = ref([])

// DiagramEditor / DiagramRuntime 期望 { id, canvas } 形态的 diagram 对象
const diagramObj = computed(() => ({ id: props.view.id, canvas: canvas.value }))

// 可绑定点位 = 本视图所属房间(分区)的成员
const targets = computed(() => {
  const sensors = []
  const devices = []
  const secs = (store.layout?.sections || []).filter(
    (sec) => props.view.section == null || sec.id === props.view.section,
  )
  for (const sec of secs) {
    for (const s of sec.sensors || []) {
      sensors.push({ id: s.point_id, name: s.sensor_name || s.tag, tag: s.tag, data_key: s.data_key, unit: s.unit })
    }
    for (const d of sec.devices || []) {
      devices.push({
        id: d.device_id,
        name: d.device_name || d.tag,
        tag: d.tag,
        data_fields: d.data_fields || [],
      })
    }
  }
  return {
    sensors,
    devices,
    automationRules: automationRules.value,
    controlSchemes: controlSchemes.value,
  }
})

function asArray(payload) {
  return payload?.results || payload || []
}

async function loadControlTargets() {
  if (!props.view.project || !props.view.section) return
  try {
    const [rulesPayload, schemesPayload] = await Promise.all([
      getAutomationRules({ project: props.view.project, section: props.view.section }),
      listControlSchemes(props.view.project, props.view.section),
    ])
    automationRules.value = asArray(rulesPayload)
    // P&ID 控制器图元支持绑定全部结构化控制方案（双位 / PI / PID）。
    controlSchemes.value = asArray(schemesPayload)
    store.upsertAutomationControls(automationRules.value, controlSchemes.value)
  } catch (error) {
    console.error('[diagram] 加载自动化图元候选项失败', error)
  }
}

watch(
  () => [props.view.project, props.view.section],
  loadControlTargets,
  { immediate: true },
)

function toggleMode() {
  mode.value = mode.value === 'edit' ? 'view' : 'edit'
}
function onCanvasChange(c) {
  canvas.value = c
  dirty.value = true
}
async function save() {
  saving.value = true
  try {
    await updateView(props.view.id, { config: canvas.value })
    dirty.value = false
    emit('saved', { id: props.view.id, config: canvas.value })
    ElMessage.success('已保存')
  } catch (e) {
    console.error('[diagram] 保存失败', e)
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped lang="scss">
.dv {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 230px);
  min-height: 480px;
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-lg);
  box-shadow: var(--iot-shadow-sm);
  overflow: hidden;
  background: var(--iot-bg-card);
}

.dv:fullscreen {
  height: 100vh;
  border-radius: 0;
  border: none;
}

.dv__bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--iot-spacing-sm) var(--iot-spacing-md);
  border-bottom: 1px solid var(--iot-border-color-light);
  flex-shrink: 0;
}

.dv__name {
  font-size: var(--iot-font-size-sm);
  font-weight: 600;
  color: var(--iot-text-primary);
}

.dv__actions {
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-sm);
}
</style>
