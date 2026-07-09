<template>
  <div class="psr">
    <div class="psr__toolbar">
      <div class="psr__summary">
        <span>脚本规则</span>
        <el-tag size="small" effect="plain">{{ rules.length }}</el-tag>
        <span class="psr__resource-count">
          {{ availableSources.sensors.length }} 个传感器 · {{ availableSources.devices.length }} 个设备
        </span>
      </div>
      <el-button v-if="isStaff" type="primary" :icon="Plus" @click="openCreate">新建脚本</el-button>
    </div>

    <el-table v-if="rules.length" v-loading="loading" :data="rules" stripe class="psr__table psr__table--desktop">
      <el-table-column label="规则" min-width="190">
        <template #default="{ row }">
          <button class="psr__rule-link" type="button" @click="openEdit(row)">{{ row.name }}</button>
          <div class="psr__script-id">{{ row.script_id }}</div>
        </template>
      </el-table-column>
      <el-table-column label="资源" min-width="180">
        <template #default="{ row }">
          <div class="psr__resources">
            <el-tag v-for="item in row.device_list.slice(0, 3)" :key="`${item.device_type}:${item.device_id}`"
                    size="small" :type="item.device_type === 'Sensor' ? 'success' : 'warning'" effect="plain">
              {{ item.name || item.device_id }}
            </el-tag>
            <span v-if="row.device_list.length > 3" class="psr__more">+{{ row.device_list.length - 3 }}</span>
            <span v-if="!row.device_list.length" class="psr__muted">无关联资源</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="周期" width="90" align="center">
        <template #default="{ row }"><span class="font-mono">{{ row.poll_interval }}s</span></template>
      </el-table-column>
      <el-table-column label="状态" width="120" align="center">
        <template #default="{ row }">
          <el-tooltip :disabled="!row.error_message" :content="row.error_message">
            <el-tag size="small" :type="statusType(row.process_status)" effect="plain">
              {{ statusLabel(row.process_status) }}
            </el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column v-if="isStaff" label="运行" width="80" align="center">
        <template #default="{ row }">
          <el-switch :model-value="row.is_launched" :loading="actionLoading[row.id]"
                     @change="(value) => toggleLaunch(row, value)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="190" align="right">
        <template #default="{ row }">
          <el-button v-if="isStaff" link type="success" :icon="VideoPlay"
                     :loading="executeLoading[row.id]" @click="execute(row)">测试</el-button>
          <el-button link type="primary" :icon="isStaff ? Edit : View" @click="openEdit(row)">
            {{ isStaff ? '编辑' : '查看' }}
          </el-button>
          <el-button v-if="isStaff" link type="danger" :icon="Delete" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="rules.length" v-loading="loading" class="psr-mobile-list">
      <article v-for="row in rules" :key="row.id" class="psr-mobile-rule">
        <div class="psr-mobile-rule__head">
          <div class="psr-mobile-rule__identity">
            <button class="psr__rule-link" type="button" @click="openEdit(row)">{{ row.name }}</button>
            <span class="psr__script-id">{{ row.script_id }}</span>
          </div>
          <el-tooltip :disabled="!row.error_message" :content="row.error_message">
            <el-tag size="small" :type="statusType(row.process_status)" effect="plain">
              {{ statusLabel(row.process_status) }}
            </el-tag>
          </el-tooltip>
        </div>

        <div class="psr-mobile-rule__resources">
          <el-tag v-for="item in row.device_list" :key="`${item.device_type}:${item.device_id}`"
                  size="small" :type="item.device_type === 'Sensor' ? 'success' : 'warning'" effect="plain">
            {{ item.name || item.device_id }}
          </el-tag>
          <span v-if="!row.device_list.length" class="psr__muted">无关联资源</span>
        </div>

        <div class="psr-mobile-rule__footer">
          <div class="psr-mobile-rule__runtime">
            <span class="font-mono">{{ row.poll_interval }}s</span>
            <el-switch v-if="isStaff" :model-value="row.is_launched" :loading="actionLoading[row.id]"
                       aria-label="轮询运行" @change="(value) => toggleLaunch(row, value)" />
          </div>
          <div class="psr-mobile-rule__actions">
            <el-button v-if="isStaff" link type="success" :icon="VideoPlay"
                       :loading="executeLoading[row.id]" @click="execute(row)">测试</el-button>
            <el-button link type="primary" :icon="isStaff ? Edit : View" @click="openEdit(row)">
              {{ isStaff ? '编辑' : '查看' }}
            </el-button>
            <el-button v-if="isStaff" link type="danger" :icon="Delete" @click="remove(row)">删除</el-button>
          </div>
        </div>
      </article>
    </div>

    <el-empty v-else :description="emptyText" />

    <section class="psr-terminal psr-terminal--board">
      <div class="psr-terminal__head">
        <span>自动化终端</span>
        <el-button v-if="terminalLines.length" link size="small" @click="clearTerminal">清空</el-button>
      </div>
      <div class="psr-terminal__body">
        <div v-if="!terminalLines.length" class="psr-terminal__empty">
          等待执行输出……点击任一脚本的“测试”后，print 与运行日志会显示在这里。
        </div>
        <div v-for="(line, index) in terminalLines" :key="index" class="psr-terminal__line"
             :class="`is-${line.type}`">
          <span>{{ line.time }}</span><strong>{{ line.type.toUpperCase() }}</strong><span>{{ line.message }}</span>
        </div>
      </div>
    </section>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="min(1040px, 94vw)" top="3vh"
               append-to-body destroy-on-close class="psr-dialog" @closed="resetDialog">
      <div v-loading="detailLoading" class="psr-editor">
        <el-form :model="form" label-position="top" class="psr-editor__meta">
          <el-form-item label="规则名称" required>
            <el-input v-model="form.name" :disabled="!isStaff" placeholder="如：高温联锁控制" />
          </el-form-item>
          <el-form-item label="脚本 ID" required>
            <el-input v-model="form.script_id" :disabled="!isStaff" placeholder="如：room_temp_interlock" />
          </el-form-item>
          <el-form-item label="轮询周期">
            <el-input-number v-model="form.poll_interval" :disabled="!isStaff" :min="1" :max="86400"
                             controls-position="right" />
            <span class="psr__unit">秒</span>
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="form.description" :disabled="!isStaff" type="textarea" :rows="2" />
          </el-form-item>
        </el-form>

        <section class="psr-editor__section">
          <div class="psr-editor__section-head">
            <h3>可用资源</h3>
            <el-button v-if="isStaff" size="small" :icon="Plus" @click="addResource">添加资源</el-button>
          </div>
          <div v-if="form.device_list.length" class="psr-resource-list">
            <div v-for="(item, index) in form.device_list" :key="index" class="psr-resource-row">
              <el-select v-model="item.device_type" :disabled="!isStaff" class="psr-resource-row__type"
                         @change="item.device_id = ''; item.name = ''">
                <el-option label="传感器" value="Sensor" />
                <el-option label="设备" value="Device" />
              </el-select>
              <el-select v-model="item.device_id" :disabled="!isStaff" filterable class="psr-resource-row__source"
                         placeholder="选择当前房间已导入的资源" @change="syncResourceName(item)">
                <el-option v-for="source in sourceOptions(item.device_type)" :key="source.id"
                           :label="`${source.name} (${source.id})`" :value="source.id" />
              </el-select>
              <span class="psr-resource-row__hint">{{ sourceHint(item) }}</span>
              <el-button v-if="isStaff" :icon="Delete" circle text type="danger"
                         title="移除资源" @click="form.device_list.splice(index, 1)" />
            </div>
          </div>
          <el-empty v-else :image-size="42" description="尚未关联资源" />
        </section>

        <section class="psr-editor__section psr-editor__code-section">
          <div class="psr-editor__section-head">
            <h3>Python 脚本</h3>
            <div class="psr-editor__code-actions">
              <span class="psr__muted">{{ scriptLineCount }} 行</span>
              <el-button v-if="isStaff && !form.script" size="small" @click="insertTemplate">插入模板</el-button>
            </div>
          </div>
          <Codemirror v-model="form.script" :disabled="!isStaff" :extensions="cmExtensions"
                      :style="{ minHeight: '360px', fontSize: '13px' }" class="psr-codemirror" />
        </section>

        <section class="psr-terminal">
          <div class="psr-terminal__head">
            <span>执行结果</span>
            <el-button v-if="terminalLines.length" link size="small" @click="clearTerminal">清空</el-button>
          </div>
          <div class="psr-terminal__body">
            <div v-if="!terminalLines.length" class="psr-terminal__empty">等待执行输出……</div>
            <div v-for="(line, index) in terminalLines" :key="index" class="psr-terminal__line"
                 :class="`is-${line.type}`">
              <span>{{ line.time }}</span><strong>{{ line.type.toUpperCase() }}</strong><span>{{ line.message }}</span>
            </div>
          </div>
        </section>
      </div>

      <template #footer>
        <el-button @click="dialogVisible = false">关闭</el-button>
        <el-button v-if="form.id && isStaff" type="success" plain :icon="VideoPlay"
                   :loading="dialogExecuting" @click="execute(form, true)">执行测试</el-button>
        <el-button v-if="isStaff" type="primary" :icon="Check" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Delete, Edit, Plus, VideoPlay, View } from '@element-plus/icons-vue'
import { Codemirror } from 'vue-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import {
  createAutomationRule, deleteAutomationRule, executeAutomationRule, getAutomationRule,
  getAutomationRules, getAvailableSources, launchAutomationRule, stopAutomationRule,
  updateAutomationRule,
} from '@/api/automation'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  projectId: { type: Number, required: true },
  sectionId: { type: [Number, null], default: null },
})

const userStore = useUserStore()
const isStaff = computed(() => userStore.userInfo?.is_staff === true)
const cmExtensions = [python(), oneDark]
const asArray = (value) => value?.results || value || []

const rules = ref([])
const availableSources = ref({ sensors: [], devices: [] })
const loading = ref(false)
const dialogVisible = ref(false)
const detailLoading = ref(false)
const saving = ref(false)
const dialogExecuting = ref(false)
const executeLoading = ref({})
const actionLoading = ref({})
const terminalLines = ref([])

function emptyForm() {
  return {
    id: null, name: '', script_id: '', description: '', script: '', poll_interval: 30,
    project: props.projectId, section: props.sectionId, device_list: [],
  }
}
const form = ref(emptyForm())

const dialogTitle = computed(() => form.value.id ? `${isStaff.value ? '编辑' : '查看'}脚本规则` : '新建脚本规则')
const scriptLineCount = computed(() => form.value.script ? form.value.script.split('\n').length : 0)
const emptyText = computed(() => {
  if (!availableSources.value.sensors.length && !availableSources.value.devices.length) {
    return '本房间尚未导入传感器或设备'
  }
  return '暂无脚本规则'
})

function statusLabel(status) {
  return { idle: '未启动', running: '运行中', stopped_by_user: '已停止', error_stopped: '错误停止' }[status] || status
}
function statusType(status) {
  return { idle: 'info', running: 'success', stopped_by_user: 'info', error_stopped: 'danger' }[status] || 'info'
}
function errorMessage(error, fallback) {
  const data = error?.response?.data
  if (typeof data?.detail === 'string') return data.detail
  if (data && typeof data === 'object') return Object.values(data).flat(Infinity).join('；')
  return fallback
}

async function loadRules({ showLoading = false } = {}) {
  if (!props.sectionId) return
  if (showLoading) loading.value = true
  try {
    rules.value = asArray(await getAutomationRules({ project: props.projectId, section: props.sectionId }))
  } catch (error) {
    console.error('[project-script] 加载脚本失败', error)
  } finally {
    if (showLoading) loading.value = false
  }
}
async function loadSources() {
  if (!props.sectionId) return
  try {
    availableSources.value = await getAvailableSources({ project: props.projectId, section: props.sectionId })
  } catch (error) {
    console.error('[project-script] 加载资源失败', error)
  }
}

function sourceOptions(type) {
  return type === 'Sensor' ? availableSources.value.sensors : availableSources.value.devices
}
function findSource(item) {
  return sourceOptions(item.device_type).find((source) => source.id === item.device_id)
}
function syncResourceName(item) {
  item.name = findSource(item)?.name || ''
}
function sourceHint(item) {
  const source = findSource(item)
  const values = item.device_type === 'Sensor' ? source?.data_fields : source?.commands
  return values?.length ? values.join(' · ') : ''
}
function addResource() {
  const type = availableSources.value.sensors.length ? 'Sensor' : 'Device'
  form.value.device_list.push({ device_id: '', device_type: type, name: '' })
}

function openCreate() {
  form.value = emptyForm()
  dialogVisible.value = true
}
async function openEdit(row) {
  dialogVisible.value = true
  detailLoading.value = true
  try {
    const detail = await getAutomationRule(row.id)
    form.value = { ...emptyForm(), ...detail, device_list: detail.device_list || [] }
  } catch (error) {
    ElMessage.error(errorMessage(error, '获取规则详情失败'))
    dialogVisible.value = false
  } finally {
    detailLoading.value = false
  }
}
function resetDialog() {
  form.value = emptyForm()
}
function insertTemplate() {
  const sensor = availableSources.value.sensors[0]?.id || 'sensor_id'
  const device = availableSources.value.devices[0]?.id || 'device_id'
  form.value.script = `from engine import sensors, devices\n\ndef loop() -> bool:\n    sensor = sensors.get('${sensor}')\n    device = devices.get('${device}')\n    if not sensor or not device:\n        return False\n\n    value = sensor.current_state.get('value')\n    if value is None:\n        return False\n    return True\n`
}
function validateForm() {
  if (!form.value.name.trim()) return '请填写规则名称'
  if (!/^[A-Za-z0-9_]+$/.test(form.value.script_id)) return '脚本 ID 仅支持字母、数字和下划线'
  if (!form.value.script.trim()) return '请填写 Python 脚本'
  if (form.value.device_list.some((item) => !item.device_id)) return '请选择所有关联资源'
  const keys = form.value.device_list.map((item) => `${item.device_type}:${item.device_id}`)
  if (new Set(keys).size !== keys.length) return '关联资源不能重复'
  return ''
}
async function save() {
  const message = validateForm()
  if (message) { ElMessage.warning(message); return }
  const payload = {
    name: form.value.name.trim(), script_id: form.value.script_id.trim(),
    description: form.value.description, script: form.value.script,
    poll_interval: form.value.poll_interval, project: props.projectId, section: props.sectionId,
    device_list: form.value.device_list,
  }
  saving.value = true
  try {
    if (form.value.id) await updateAutomationRule(form.value.id, payload)
    else await createAutomationRule(payload)
    ElMessage.success('脚本规则已保存')
    dialogVisible.value = false
    await loadRules()
  } catch (error) {
    ElMessage.error(errorMessage(error, '保存失败'))
  } finally {
    saving.value = false
  }
}

function appendResult(result) {
  const now = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  const ruleLabel = result.rule_name ? ` [${result.rule_name}]` : ''
  terminalLines.value.push({ time: now, type: 'command', message: `> 执行${ruleLabel}` })
  if (result.output) result.output.replace(/\n$/, '').split('\n').forEach((message) => terminalLines.value.push({ time: now, type: 'info', message }))
  for (const log of result.logs || []) {
    const type = log.level === 'ERROR' ? 'error' : (log.level === 'WARNING' ? 'warning' : 'info')
    terminalLines.value.push({ time: now, type, message: log.message })
  }
  terminalLines.value.push({ time: now, type: result.success ? 'success' : 'error', message: result.success ? '执行成功' : (result.error || '执行未满足条件') })
  if (terminalLines.value.length > 500) terminalLines.value.splice(0, terminalLines.value.length - 500)
}
function clearTerminal() {
  terminalLines.value = []
}
async function execute(row, inDialog = false) {
  if (inDialog) dialogExecuting.value = true
  else executeLoading.value[row.id] = true
  try {
    const result = await executeAutomationRule(row.id)
    appendResult(result)
    ElMessage[result.success ? 'success' : 'warning'](result.success ? '执行成功' : '执行未满足条件')
  } catch (error) {
    const message = errorMessage(error, '执行失败')
    const result = error?.response?.data
    if (result && typeof result === 'object' && ('output' in result || 'logs' in result)) appendResult(result)
    else terminalLines.value.push({ time: new Date().toLocaleTimeString('zh-CN', { hour12: false }), type: 'error', message })
    ElMessage.error(message)
  } finally {
    if (inDialog) dialogExecuting.value = false
    else executeLoading.value[row.id] = false
    await loadRules()
  }
}
async function toggleLaunch(row, value) {
  actionLoading.value[row.id] = true
  try {
    if (value) await launchAutomationRule(row.id, row.poll_interval)
    else await stopAutomationRule(row.id)
    ElMessage.success(value ? '轮询已启动' : '轮询已停止')
  } catch (error) {
    ElMessage.error(errorMessage(error, '操作失败'))
  } finally {
    actionLoading.value[row.id] = false
    await loadRules()
  }
}
async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除脚本规则“${row.name}”？`, '删除确认', { type: 'warning' })
    await deleteAutomationRule(row.id)
    ElMessage.success('脚本规则已删除')
    await loadRules()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error, '删除失败'))
  }
}

let timer = null
async function reload() {
  await Promise.all([loadRules({ showLoading: true }), loadSources()])
}
onMounted(() => {
  reload()
  timer = setInterval(() => loadRules(), 4000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
watch(() => props.sectionId, reload)
</script>

<style scoped lang="scss">
.psr { width: 100%; }
.psr__toolbar, .psr__summary, .psr-editor__section-head, .psr-editor__code-actions,
.psr-terminal__head { display: flex; align-items: center; }
.psr__toolbar {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: var(--iot-spacing-md);
}
.psr__summary { gap: 8px; font-weight: 600; color: var(--iot-text-primary); }
.psr__resource-count, .psr__muted, .psr__more { color: var(--iot-text-secondary); font-size: 12px; font-weight: 400; }
.psr__rule-link { border: 0; padding: 0; background: none; color: var(--iot-color-primary); cursor: pointer; font: inherit; font-weight: 600; }
.psr__script-id { margin-top: 4px; color: var(--iot-text-secondary); font: 12px var(--iot-font-mono, monospace); }
.psr__resources { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.psr__unit { margin-left: 8px; color: var(--iot-text-secondary); }
.psr-mobile-list { display: none; }
.psr__table {
  border-radius: var(--iot-radius-lg);
  overflow: hidden;
}
.psr__table :deep(.el-table__header-wrapper) {
  margin-bottom: 8px;
  overflow: hidden;
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-base);
}
.psr__table :deep(.el-table__header-wrapper th.el-table__cell) {
  border-bottom: 0;
  background: var(--iot-bg-card);
}
.psr-editor__meta { display: grid; grid-template-columns: 1fr 1fr 150px; gap: 0 14px; }
.psr-editor__meta :deep(.el-form-item:last-child) { grid-column: 1 / -1; }
.psr-editor__section { border-top: 1px solid var(--iot-border-color); padding-top: 16px; margin-top: 2px; }
.psr-editor__section-head { justify-content: space-between; margin-bottom: 12px; }
.psr-editor__section-head h3 { margin: 0; font-size: 14px; letter-spacing: 0; }
.psr-resource-list { display: grid; gap: 8px; }
.psr-resource-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.psr-resource-row__type { width: 110px; flex: 0 0 110px; }
.psr-resource-row__source { width: 320px; flex: 0 1 320px; }
.psr-resource-row__hint { flex: 1; min-width: 0; color: var(--iot-text-secondary); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.psr-codemirror { border: 1px solid #363b46; overflow: hidden; border-radius: 4px; }
.psr-codemirror :deep(.cm-editor) { min-height: 360px; }
.psr-terminal { margin-top: 16px; background: #1f232b; color: #d8dee9; border-radius: 4px; overflow: hidden; }
.psr-terminal--board { margin-top: 20px; border: 1px solid #343b47; box-shadow: var(--iot-shadow-sm); }
.psr-terminal__head { justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid #3a404b; font-size: 12px; }
.psr-terminal__body { min-height: 96px; max-height: 280px; overflow: auto; padding: 6px 0; }
.psr-terminal__empty { padding: 18px 12px; color: #7f8998; font: 12px/1.6 var(--iot-font-mono, monospace); }
.psr-terminal__line { display: grid; grid-template-columns: 72px 58px 1fr; gap: 8px; padding: 4px 12px; font: 12px/1.5 var(--iot-font-mono, monospace); }
.psr-terminal__line > span:last-child { min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.psr-terminal__line.is-command strong { color: #8fb7ff; }
.psr-terminal__line.is-warning strong { color: #f0c36a; }
.psr-terminal__line.is-error strong { color: #ff8a7a; }
.psr-terminal__line.is-success strong { color: #80c99b; }
:global(.psr-dialog) {
  display: flex;
  max-height: 94vh;
  flex-direction: column;
  overflow: hidden;
}
:global(.psr-dialog .el-dialog__body) {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
}
:global(.psr-dialog .el-dialog__header),
:global(.psr-dialog .el-dialog__footer) {
  flex: 0 0 auto;
}
@media (max-width: 760px) {
  .psr__toolbar { align-items: flex-start; }
  .psr__summary { flex-wrap: wrap; row-gap: 4px; }
  .psr__resource-count { flex-basis: 100%; }
  .psr__table--desktop { display: none; }
  .psr-mobile-list { display: grid; gap: 10px; }
  .psr-mobile-rule {
    padding: 12px;
    border: 1px solid var(--iot-border-color-light);
    border-radius: var(--iot-radius-base);
    background: var(--iot-bg-card);
  }
  .psr-mobile-rule__head,
  .psr-mobile-rule__footer,
  .psr-mobile-rule__runtime,
  .psr-mobile-rule__actions {
    display: flex;
    align-items: center;
  }
  .psr-mobile-rule__head,
  .psr-mobile-rule__footer { justify-content: space-between; gap: 10px; }
  .psr-mobile-rule__identity { min-width: 0; }
  .psr-mobile-rule__identity .psr__script-id {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .psr-mobile-rule__resources {
    display: flex;
    min-height: 26px;
    align-items: center;
    gap: 5px;
    margin: 10px 0;
    flex-wrap: wrap;
  }
  .psr-mobile-rule__footer {
    padding-top: 10px;
    border-top: 1px solid var(--iot-border-color-lighter);
  }
  .psr-mobile-rule__runtime { gap: 10px; color: var(--iot-text-secondary); }
  .psr-mobile-rule__actions { gap: 2px; }
  .psr-mobile-rule__actions :deep(.el-button + .el-button) { margin-left: 2px; }
  .psr-editor__meta { grid-template-columns: 1fr; }
  .psr-editor__meta :deep(.el-form-item:last-child) { grid-column: auto; }
  .psr-resource-row {
    display: grid;
    grid-template-columns: 100px minmax(0, 1fr) 32px;
    align-items: center;
  }
  .psr-resource-row__type { width: 100px; }
  .psr-resource-row__source { width: 100%; }
  .psr-resource-row__hint {
    grid-column: 2 / 4;
    grid-row: 2;
    padding-left: 0;
    overflow: visible;
    text-overflow: clip;
    white-space: normal;
  }
  .psr-resource-row > :deep(.el-button) { grid-column: 3; grid-row: 1; }
  .psr-codemirror { min-height: 280px !important; }
  .psr-codemirror :deep(.cm-editor) { min-height: 280px; }
  :global(.psr-dialog) {
    width: calc(100vw - 20px) !important;
    max-height: calc(100vh - 20px);
    margin: 10px auto !important;
  }
  :global(.psr-dialog .el-dialog__body) { padding: 12px 16px; }
  :global(.psr-dialog .el-dialog__header),
  :global(.psr-dialog .el-dialog__footer) { padding-left: 16px; padding-right: 16px; }
}
</style>
