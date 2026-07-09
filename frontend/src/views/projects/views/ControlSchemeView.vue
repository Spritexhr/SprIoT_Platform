<template>
  <div class="csv">
    <!-- 工具栏 -->
    <div class="csv__toolbar">
      <div class="csv__summary">
        <span>控制方案</span>
        <el-tag size="small" effect="plain">{{ schemes.length }}</el-tag>
        <span class="csv__resource-count">
          选模板（双位 / PI / PID）→ 绑定点位 → 启用自动闭环控制
        </span>
      </div>
      <el-button v-if="isStaff" type="primary" :icon="Plus" @click="openCreate">新建控制方案</el-button>
    </div>

    <!-- 列表 -->
    <el-table
      v-if="schemes.length"
      :data="schemes"
      class="csv__table"
      stripe
      size="default"
    >
      <el-table-column label="方案" min-width="160">
        <template #default="{ row }">
          <div class="csv-cell-name">
            <span class="csv-cell-name__title">{{ row.name }}</span>
            <el-tag size="small" :type="typeTag(row.control_type)" effect="plain" round>
              {{ row.control_type_display }}
            </el-tag>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="绑定（PV → 执行器）" min-width="200">
        <template #default="{ row }">
          <span class="font-mono">{{ row.sensor_tag || row.sensor_id }}</span>
          <span class="csv-arrow">→</span>
          <span class="font-mono">{{ row.device_tag || row.device_id }}</span>
        </template>
      </el-table-column>

      <el-table-column label="设定值" width="90" align="center">
        <template #default="{ row }"><span class="font-mono">{{ row.setpoint }}</span></template>
      </el-table-column>

      <el-table-column label="实测 PV" width="90" align="center">
        <template #default="{ row }">
          <span class="font-mono">{{ fmt(row.last_pv) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="输出" width="90" align="center">
        <template #default="{ row }">
          <span class="font-mono">{{ fmt(row.last_output) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="状态" width="120" align="center">
        <template #default="{ row }">
          <el-tooltip v-if="row.status === 'error'" :content="row.error_message" placement="top">
            <el-tag size="small" type="danger" effect="dark">错误</el-tag>
          </el-tooltip>
          <el-tag v-else size="small" :type="row.status === 'running' ? 'success' : 'info'" effect="plain">
            {{ row.status_display }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="运行" width="80" align="center">
        <template #default="{ row }">
          <el-switch
            :model-value="row.is_enabled"
            :disabled="!isStaff"
            @change="(v) => toggleEnable(row, v)"
          />
        </template>
      </el-table-column>

      <el-table-column v-if="isStaff" label="操作" width="190" align="right">
        <template #default="{ row }">
          <el-button link type="success" size="small" :icon="VideoPlay" @click="testStep(row)">测试</el-button>
          <el-button link type="primary" size="small" :icon="Edit" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" size="small" :icon="Delete" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-else :description="emptyText" />

    <!-- 创建 / 编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑控制方案' : '新建控制方案'"
      width="640px"
      top="5vh"
      append-to-body
      class="csv-dialog"
      @closed="onDialogClosed"
    >
      <!-- 模板选择（仅新建） -->
      <div v-if="!form.id" class="csv-templates">
        <div
          v-for="t in templates"
          :key="t.control_type"
          class="csv-template-card"
          :class="{ 'is-active': form.control_type === t.control_type }"
          @click="applyTemplate(t)"
        >
          <div class="csv-template-card__name">{{ t.name }}</div>
          <div class="csv-template-card__desc">{{ t.description }}</div>
        </div>
      </div>

      <el-form v-if="currentTemplate" :model="form" label-width="92px" class="csv-form">
        <el-divider content-position="left">基本</el-divider>
        <el-form-item label="方案名称" required>
          <el-input v-model="form.name" placeholder="如：反应釜温度控制" />
        </el-form-item>
        <el-form-item label="作用方向">
          <el-radio-group v-model="form.action">
            <el-radio value="cool">反作用（PV 高于 SP 时增大输出/开）</el-radio>
            <el-radio value="heat">正作用（PV 低于 SP 时增大输出/开）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="设定值 SP">
          <el-input-number v-model="form.setpoint" :step="0.5" controls-position="right" />
        </el-form-item>
        <el-form-item label="控制周期">
          <el-input-number v-model="form.sample_interval" :min="1" :step="1" controls-position="right" />
          <span class="csv-unit">秒</span>
        </el-form-item>

        <el-divider content-position="left">被控量（PV）</el-divider>
        <el-form-item label="传感器" required>
          <el-select v-model="form.sensor_member" placeholder="选择传感器成员" style="width: 100%"
                     @change="onSensorChange">
            <el-option v-for="m in sensorMembers" :key="m.id"
                       :label="`${m.tag || m.sensor_id}（${m.sensor_name}）`" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="数据字段">
          <el-select v-model="form.data_key" placeholder="取哪个字段作为 PV" style="width: 100%">
            <el-option v-for="f in sensorDataFields" :key="f" :label="f" :value="f" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">执行器与输出</el-divider>
        <el-form-item label="设备" required>
          <el-select v-model="form.device_member" placeholder="选择设备成员" style="width: 100%"
                     @change="onDeviceChange">
            <el-option v-for="m in deviceMembers" :key="m.id"
                       :label="`${m.tag || m.device_id}（${m.device_name}）`" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="currentTemplate.output_modes.length > 1" label="输出方式">
          <el-radio-group v-model="form.output_mode">
            <el-radio v-for="om in currentTemplate.output_modes" :key="om" :value="om">
              {{ om === 'analog' ? '模拟量（下发数值）' : '开关量（开/关）' }}
            </el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- 模拟量命令映射 -->
        <template v-if="form.output_mode === 'analog'">
          <el-form-item label="数值命令">
            <el-select v-model="form.params.analog.command" placeholder="选择下发命令" style="width: 100%">
              <el-option v-for="c in deviceCommandNames" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item label="数值参数">
            <el-select v-model="form.params.analog.param" placeholder="该命令的数值参数名"
                       filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="p in analogParamOptions" :key="p" :label="p" :value="p" />
            </el-select>
          </el-form-item>
          <el-form-item label="执行器量程">
            <el-input-number v-model="form.params.analog.range_min" controls-position="right" />
            <span class="csv-unit">~</span>
            <el-input-number v-model="form.params.analog.range_max" controls-position="right" />
            <span class="csv-tip">输出 0–100% 线性映射到此范围（如舵机 0–180°）</span>
          </el-form-item>
        </template>

        <!-- 开关量命令映射 -->
        <template v-else>
          <el-form-item label="开命令">
            <el-select v-model="form.params.switch.on_command" placeholder="ON 命令" style="width: 100%">
              <el-option v-for="c in deviceCommandNames" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item label="关命令">
            <el-select v-model="form.params.switch.off_command" placeholder="OFF 命令" style="width: 100%">
              <el-option v-for="c in deviceCommandNames" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="form.control_type !== 'on_off'" label="转换方式">
            <el-radio-group v-model="form.params.switch.convert">
              <el-radio value="threshold">阈值（输出过半即开）</el-radio>
              <el-radio value="pwm">时间比例 PWM</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="form.control_type !== 'on_off' && form.params.switch.convert === 'pwm'" label="PWM周期">
            <el-input-number v-model="form.params.switch.pwm_period" :min="1" controls-position="right" />
            <span class="csv-unit">秒</span>
          </el-form-item>
        </template>

        <el-divider content-position="left">控制参数</el-divider>
        <el-form-item v-for="f in currentTemplate.param_fields" :key="f.key" :label="f.label">
          <el-input-number v-model="form.params[f.key]" :step="0.1" controls-position="right" />
          <span v-if="f.help" class="csv-tip">{{ f.help }}</span>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">关闭</el-button>
        <el-button type="primary" :icon="Check" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Cpu, Delete, Edit, Plus, VideoPlay } from '@element-plus/icons-vue'

import {
  listControlSchemes, createControlScheme, updateControlScheme, deleteControlScheme,
  enableControlScheme, disableControlScheme, stepControlScheme, getControlTemplates,
} from '@/api/controlSchemes'
import { listSensorMembers, listDeviceMembers } from '@/api/projects'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  projectId: { type: Number, required: true },
  sectionId: { type: [Number, null], default: null },
})

const userStore = useUserStore()
const isStaff = computed(() => userStore.userInfo?.is_staff === true)

const asArray = (r) => (r && r.results) || r || []

const schemes = ref([])
const sensorMembers = ref([])
const deviceMembers = ref([])
const templates = ref([])

const dialogVisible = ref(false)
const saving = ref(false)

function emptyForm() {
  return {
    id: null, name: '', control_type: 'on_off', setpoint: 0, action: 'cool',
    sample_interval: 5, output_mode: 'switch',
    sensor_member: null, data_key: '', device_member: null,
    params: {},
  }
}
const form = ref(emptyForm())

const currentTemplate = computed(
  () => templates.value.find((t) => t.control_type === form.value.control_type) || null,
)

const selectedSensorMember = computed(
  () => sensorMembers.value.find((m) => m.id === form.value.sensor_member) || null,
)
const sensorDataFields = computed(() => selectedSensorMember.value?.data_fields || [])

const selectedDeviceMember = computed(
  () => deviceMembers.value.find((m) => m.id === form.value.device_member) || null,
)
const deviceCommandNames = computed(() => Object.keys(selectedDeviceMember.value?.commands || {}))
const analogParamOptions = computed(() => {
  const cmds = selectedDeviceMember.value?.commands || {}
  const cmd = cmds[form.value.params?.analog?.command]
  return (cmd && cmd.params) || []
})

const emptyText = computed(() => {
  if (!sensorMembers.value.length || !deviceMembers.value.length) {
    return '本房间还没有可绑定的传感器或设备，请先到配置页导入成员'
  }
  return '暂无控制方案，点击右上角「新建控制方案」开始'
})

const fmt = (v) => (v === null || v === undefined ? '—' : v)
function typeTag(t) {
  return { on_off: 'warning', pi: 'success', pid: 'primary' }[t] || 'info'
}

const deepClone = (o) => JSON.parse(JSON.stringify(o || {}))

// ---------------- 数据加载 ----------------
async function loadSchemes() {
  try {
    schemes.value = asArray(await listControlSchemes(props.projectId, props.sectionId))
  } catch (e) {
    console.error('[control] 加载方案失败', e)
  }
}
async function loadBindables() {
  try {
    const [sm, dm] = await Promise.all([
      listSensorMembers(props.projectId, props.sectionId),
      listDeviceMembers(props.projectId, props.sectionId),
    ])
    sensorMembers.value = asArray(sm)
    deviceMembers.value = asArray(dm)
  } catch (e) {
    console.error('[control] 加载可绑定成员失败', e)
  }
}
async function loadTemplates() {
  if (templates.value.length) return
  try {
    const r = await getControlTemplates()
    templates.value = r.templates || []
  } catch (e) {
    console.error('[control] 加载模板失败', e)
  }
}

// ---------------- 表单 ----------------
function applyTemplate(t) {
  const d = t.defaults || {}
  form.value.control_type = t.control_type
  form.value.action = d.action || 'cool'
  form.value.sample_interval = d.sample_interval || 5
  form.value.output_mode = d.output_mode || (t.output_modes[0] || 'switch')
  form.value.params = deepClone(d.params)
}

function openCreate() {
  form.value = emptyForm()
  if (templates.value[0]) applyTemplate(templates.value[0])
  dialogVisible.value = true
}

function openEdit(row) {
  form.value = {
    id: row.id, name: row.name, control_type: row.control_type,
    setpoint: row.setpoint, action: row.action, sample_interval: row.sample_interval,
    output_mode: row.output_mode,
    sensor_member: row.sensor_member, data_key: row.data_key, device_member: row.device_member,
    params: deepClone(row.params),
  }
  dialogVisible.value = true
}

function onSensorChange() {
  // 默认取传感器成员自身的 data_key
  if (selectedSensorMember.value) form.value.data_key = selectedSensorMember.value.data_key || ''
}
function onDeviceChange() {
  // 切换设备后清空已选命令，避免残留无效命令名
  if (form.value.params?.analog) form.value.params.analog.command = ''
  if (form.value.params?.switch) {
    form.value.params.switch.on_command = ''
    form.value.params.switch.off_command = ''
  }
}

function onDialogClosed() {
  form.value = emptyForm()
}

function validate() {
  const f = form.value
  if (!f.name?.trim()) return '请填写方案名称'
  if (!f.sensor_member) return '请选择被控量传感器'
  if (!f.device_member) return '请选择执行器设备'
  if (f.control_type === 'on_off' || f.output_mode === 'switch') {
    if (!f.params.switch?.on_command || !f.params.switch?.off_command) return '请配置开/关命令'
  } else if (!f.params.analog?.command) {
    return '请配置数值命令'
  }
  return null
}

async function submit() {
  const err = validate()
  if (err) { ElMessage.warning(err); return }
  const f = form.value
  const payload = {
    project: props.projectId, section: props.sectionId,
    name: f.name, sensor_member: f.sensor_member, data_key: f.data_key,
    device_member: f.device_member, control_type: f.control_type,
    setpoint: f.setpoint, action: f.action, sample_interval: f.sample_interval,
    output_mode: f.output_mode, params: f.params,
  }
  saving.value = true
  try {
    if (f.id) await updateControlScheme(f.id, payload)
    else await createControlScheme(payload)
    ElMessage.success('已保存')
    dialogVisible.value = false
    await loadSchemes()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败，请检查参数与权限')
  } finally {
    saving.value = false
  }
}

async function toggleEnable(row, val) {
  try {
    if (val) await enableControlScheme(row.id)
    else await disableControlScheme(row.id)
    await loadSchemes()
  } catch (e) {
    ElMessage.error('操作失败，请检查权限')
    await loadSchemes()
  }
}

async function testStep(row) {
  try {
    const r = await stepControlScheme(row.id, true)
    if (r.error) ElMessage.warning(r.error)
    else ElMessage.success(`PV=${fmt(r.pv)} 输出=${fmt(r.output)} 命令=${r.command || '无'} 下发=${r.sent ? '成功' : '未确认'}`)
    await loadSchemes()
  } catch (e) {
    ElMessage.error('试运行失败')
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除控制方案「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteControlScheme(row.id)
    ElMessage.success('已删除')
    await loadSchemes()
  } catch (e) {
    ElMessage.error('删除失败，请检查权限')
  }
}

// ---------------- 生命周期 / 轮询 ----------------
let timer = null
async function reload() {
  await Promise.all([loadSchemes(), loadBindables(), loadTemplates()])
}
onMounted(() => {
  reload()
  timer = setInterval(loadSchemes, 4000) // 轮询刷新 PV/输出/状态
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
watch(() => props.sectionId, () => { reload() })
</script>

<style scoped lang="scss">
.csv {
  width: 100%;
}

.csv__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: var(--iot-spacing-md);
  flex-wrap: wrap;
}

.csv__summary {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-weight: 600;
  color: var(--iot-text-primary);
}

.csv__resource-count {
  font-size: var(--iot-font-size-xs);
  font-weight: 400;
  color: var(--iot-text-secondary);
  margin-left: 4px;
}

.csv__table {
  border-radius: var(--iot-radius-lg);
  overflow: hidden;
}

.csv__table :deep(.el-table__header-wrapper) {
  margin-bottom: 8px;
  overflow: hidden;
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-base);
}

.csv__table :deep(.el-table__header-wrapper th.el-table__cell) {
  border-bottom: 0;
  background: var(--iot-bg-card);
}

.csv-cell-name {
  display: flex;
  align-items: center;
  gap: 8px;

  &__title {
    font-weight: 600;
    color: var(--iot-text-primary);
  }
}

.csv-arrow {
  margin: 0 8px;
  color: var(--iot-color-primary);
  font-weight: 600;
}

.font-mono {
  font-family: var(--iot-font-mono, ui-monospace, 'SFMono-Regular', monospace);
}

/* 模板卡 */
.csv-templates {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  align-items: stretch;        /* 三张卡等高 */
  gap: 10px;
  margin-bottom: var(--iot-spacing-md);
}

.csv-template-card {
  display: flex;
  flex-direction: column;
  height: 100%;                /* 占满栅格高度，选中描边贴合整张卡 */
  box-sizing: border-box;
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-base);
  padding: 12px;
  cursor: pointer;
  transition: all var(--iot-transition-fast);
  background: var(--iot-bg-card);

  &:hover {
    border-color: var(--iot-color-primary);
  }

  &.is-active {
    border-color: var(--iot-color-primary);
    /* 用 inset 内描边，不会被外层 overflow 裁剪（避免选中框被"拦腰截断"） */
    box-shadow: inset 0 0 0 1px var(--iot-color-primary);
    background: var(--iot-color-primary-bg);
  }

  &__name {
    font-weight: 600;
    color: var(--iot-text-primary);
    margin-bottom: 4px;
  }

  &__desc {
    font-size: 12px;
    color: var(--iot-text-secondary);
    line-height: 1.4;
  }
}

.csv-form {
  padding-right: 6px;
}

.csv-unit {
  margin: 0 8px;
  color: var(--iot-text-secondary);
}

.csv-tip {
  margin-left: 10px;
  font-size: 12px;
  color: var(--iot-text-placeholder);
}

@media (max-width: 760px) {
  .csv__toolbar {
    align-items: flex-start;
  }

  .csv__summary {
    row-gap: 4px;
  }

  .csv__resource-count {
    flex-basis: 100%;
    margin-left: 0;
  }

  .csv__toolbar :deep(.el-button) {
    width: 100%;
  }
}
</style>

<!-- 非 scoped：el-dialog 会 teleport 到 body，需用全局样式限定整个弹窗在视口内、主体整体滚动，
     避免内容（模板卡 + 表单 + 底部按钮）超出视口被截断。csv-dialog 类名足够具体不会外泄。 -->
<style lang="scss">
.csv-dialog.el-dialog {
  margin: 5vh auto !important;
  display: flex;
  flex-direction: column;
  max-height: 90vh;
}
.csv-dialog .el-dialog__body {
  flex: 1 1 auto;
  overflow-y: auto;
}
</style>
