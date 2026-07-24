/* IoT 虚拟节点模拟器 webui 前端 —— Vue 3 global build，无构建步骤 */
/* global Vue */
const { createApp, ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } = Vue;

/* ============ 工具 ============ */

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && data.detail) ? data.detail : `HTTP ${res.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

function clone(obj) { return JSON.parse(JSON.stringify(obj ?? null)); }

function parseNumericInput(raw, integer = false) {
  if (raw === '' || raw === null || raw === undefined) return undefined;
  const value = Number(raw);
  if (!Number.isFinite(value)) return undefined;
  return integer ? Math.trunc(value) : value;
}

/* 固定种子伪随机（random_walk / uniform 预览用，保证重绘稳定） */
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* 在 canvas 上绘制波形预览 */
function drawWaveform(canvas, cfg) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);
  if (!cfg || !cfg.type) return;

  const pad = { l: 6, r: 6, t: 10, b: 10 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;

  // 计算值域与采样序列
  let lo, hi, points = [], dashes = [];
  const N = 160;
  if (cfg.type === 'sine') {
    const min = +cfg.min || 0, max = +cfg.max || 0, period = +cfg.period || 1;
    const jitter = +cfg.jitter || 0, phase = +cfg.phase || 0;
    lo = min - jitter; hi = max + jitter;
    const span = period * 2;
    const amp = (max - min) / 2, center = (max + min) / 2;
    const rnd = mulberry32(7);
    for (let i = 0; i < N; i++) {
      const t = (i / (N - 1)) * span;
      let v = center + amp * Math.sin(2 * Math.PI * (t + phase) / period);
      if (jitter) v += (rnd() * 2 - 1) * jitter;
      points.push(v);
    }
  } else if (cfg.type === 'random_walk') {
    const start = +cfg.start || 0, step = +cfg.step || 0;
    const b = Array.isArray(cfg.bounds) ? cfg.bounds : [start - 10, start + 10];
    lo = +b[0]; hi = +b[1];
    dashes = [lo, hi];
    const rnd = mulberry32(42);
    let v = start;
    for (let i = 0; i < N; i++) {
      points.push(v);
      v += (rnd() * 2 - 1) * step;
      v = Math.max(lo, Math.min(hi, v));
    }
  } else if (cfg.type === 'uniform') {
    lo = +cfg.min || 0; hi = +cfg.max || 0;
    const rnd = mulberry32(99);
    for (let i = 0; i < N; i++) points.push(lo + rnd() * (hi - lo));
  } else if (cfg.type === 'constant') {
    const v = +cfg.value || 0;
    lo = v - 1; hi = v + 1;
    for (let i = 0; i < N; i++) points.push(v);
  } else { return; }

  if (!(hi > lo)) { hi = lo + 1; }
  const y = (v) => pad.t + ih - ((v - lo) / (hi - lo)) * ih;
  const x = (i) => pad.l + (i / (N - 1)) * iw;

  // 上下界虚线（random_walk bounds）
  ctx.strokeStyle = '#d9cfc2'; ctx.setLineDash([4, 3]); ctx.lineWidth = 1;
  for (const dv of dashes) {
    ctx.beginPath(); ctx.moveTo(pad.l, y(dv)); ctx.lineTo(W - pad.r, y(dv)); ctx.stroke();
  }
  ctx.setLineDash([]);

  // 曲线下方柔和渐变区域
  if (points.length > 0) {
    const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
    grad.addColorStop(0, 'rgba(217, 106, 67, 0.18)');
    grad.addColorStop(1, 'rgba(217, 106, 67, 0.00)');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.moveTo(x(0), H - pad.b);
    points.forEach((v, i) => {
      ctx.lineTo(x(i), y(v));
    });
    ctx.lineTo(x(points.length - 1), H - pad.b);
    ctx.closePath();
    ctx.fill();
  }

  // 曲线
  ctx.strokeStyle = '#d97757'; ctx.lineWidth = 1.8;
  ctx.beginPath();
  points.forEach((v, i) => { i === 0 ? ctx.moveTo(x(i), y(v)) : ctx.lineTo(x(i), y(v)); });
  ctx.stroke();

  // 值域标注
  ctx.fillStyle = '#8b7b6b'; ctx.font = '10px Menlo, monospace';
  ctx.fillText(String(+hi.toFixed(3)), pad.l + 2, pad.t + 8);
  ctx.fillText(String(+lo.toFixed(3)), pad.l + 2, H - 2);
}

const DEFAULT_WF = { type: 'sine', min: 0, max: 100, period: 600, jitter: 0, phase: 0 };

function defaultWaveformOf(type, schemas) {
  const out = { type };
  for (const p of (schemas[type]?.params || [])) {
    if (p.required) {
      if (p.type === 'range') out[p.name] = [0, 100];
      else out[p.name] = (p.name === 'period') ? 600 : (p.name === 'max' ? 100 : (p.name === 'step' ? 1 : 0));
    } else if (p.default !== undefined) {
      out[p.name] = p.default;
    }
  }
  if (type === 'random_walk') { out.start = 50; out.step = 1; out.bounds = [0, 100]; }
  return out;
}

/* ============ 波形编辑器组件 ============ */

const WaveformEditor = {
  name: 'WaveformEditor',
  props: { modelValue: Object, schemas: Object, fieldName: String },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const canvasEl = ref(null);
    const wf = computed(() => props.modelValue || {});
    const schema = computed(() => props.schemas[wf.value.type] || null);

    function setType(t) {
      emit('update:modelValue', defaultWaveformOf(t, props.schemas));
    }
    function setParam(name, raw, ptype) {
      const next = clone(wf.value);
      if (ptype === 'range') return; // range 由 setRange 处理
      next[name] = parseNumericInput(raw);
      if (next[name] === undefined) delete next[name];
      emit('update:modelValue', next);
    }
    function setRange(name, idx, raw) {
      const next = clone(wf.value);
      const cur = Array.isArray(next[name]) ? next[name] : [0, 100];
      const value = parseNumericInput(raw);
      if (value === undefined) return;
      cur[idx] = value;
      next[name] = cur;
      emit('update:modelValue', next);
    }
    function redraw() { nextTick(() => drawWaveform(canvasEl.value, wf.value)); }
    watch(wf, redraw, { deep: true, immediate: false });
    onMounted(redraw);

    return { canvasEl, wf, schema, setType, setParam, setRange };
  },
  template: `
  <div class="wf-editor">
    <div class="wf-head">
      <span v-if="fieldName" class="fname">{{ fieldName }}</span>
      <select :value="wf.type" @change="setType($event.target.value)">
        <option v-for="(s, t) in schemas" :key="t" :value="t">{{ s.label }} ({{ t }})</option>
      </select>
      <span class="wf-hint" v-if="schema">{{ schema.help }}</span>
    </div>
    <div class="wf-params" v-if="schema">
      <template v-for="p in schema.params" :key="p.name">
        <template v-if="p.type === 'range'">
          <label class="fl">{{ p.label }} 下界
            <input type="number" step="any" :value="(wf[p.name] || [0, 0])[0]"
                   @input="setRange(p.name, 0, $event.target.value)">
          </label>
          <label class="fl">{{ p.label }} 上界
            <input type="number" step="any" :value="(wf[p.name] || [0, 0])[1]"
                   @input="setRange(p.name, 1, $event.target.value)">
          </label>
        </template>
        <label v-else class="fl" :title="p.help || ''">{{ p.label }}{{ p.required ? ' *' : '' }}
          <input type="number" step="any" :value="wf[p.name]"
                 :placeholder="p.default !== undefined ? String(p.default) : ''"
                 @input="setParam(p.name, $event.target.value, p.type)">
        </label>
      </template>
    </div>
    <canvas ref="canvasEl" class="wf-canvas" role="img" :aria-label="(fieldName || '数据') + '波形预览'"></canvas>
    <div class="wf-hint" v-if="wf.type === 'random_walk'">随机游走为示意轨迹，实际运行轨迹不同；虚线为上下界。</div>
  </div>`
};

/* ============ 参数动态表单组件 ============ */

const ParamForm = {
  name: 'ParamForm',
  components: { WaveformEditor },
  props: { schema: Array, params: Object, waveforms: Object },
  setup(props) {
    // dict 类型参数用 JSON 文本缓冲
    const jsonBufs = reactive({});
    for (const spec of props.schema) {
      if (spec.type === 'dict') {
        jsonBufs[spec.name] = JSON.stringify(props.params[spec.name] ?? spec.default ?? {}, null, 0);
      }
    }
    const jsonErrs = reactive({});

    function onJson(name) {
      try {
        props.params[name] = JSON.parse(jsonBufs[name]);
        jsonErrs[name] = '';
      } catch (e) { jsonErrs[name] = 'JSON 格式错误'; }
    }

    /* --- waveform_map：逐字段"默认/自定义"开关 --- */
    function wfEnabled(spec, f) {
      return !!(props.params[spec.name] && props.params[spec.name][f]);
    }
    function toggleWf(spec, f, on) {
      if (!props.params[spec.name]) props.params[spec.name] = {};
      if (on) {
        const def = (spec.default && spec.default[f]) ? clone(spec.default[f]) : clone(DEFAULT_WF);
        props.params[spec.name][f] = def;
      } else {
        delete props.params[spec.name][f];
        if (!Object.keys(props.params[spec.name]).length) delete props.params[spec.name];
      }
    }

    /* --- fields_map（generic_sensor）：行编辑 --- */
    const fieldRows = ref([]);
    const fmSpec = props.schema.find(s => s.type === 'fields_map');
    if (fmSpec) {
      const existing = props.params[fmSpec.name] || {};
      fieldRows.value = Object.entries(existing).map(([name, cfg]) => ({
        name, waveform: clone(cfg.waveform || DEFAULT_WF),
        precision: cfg.precision ?? 2, unit: cfg.unit || '',
      }));
      if (!fieldRows.value.length) {
        fieldRows.value = [{ name: 'value', waveform: clone(DEFAULT_WF), precision: 2, unit: '' }];
      }
      watch(fieldRows, () => {
        const out = {};
        for (const r of fieldRows.value) {
          if (!r.name) continue;
          out[r.name] = { waveform: clone(r.waveform), precision: +r.precision, unit: r.unit || undefined };
          if (!out[r.name].unit) delete out[r.name].unit;
        }
        props.params[fmSpec.name] = out;
      }, { deep: true, immediate: true });
    }
    function addFieldRow() {
      fieldRows.value.push({ name: '', waveform: clone(DEFAULT_WF), precision: 2, unit: '' });
    }

    /* --- state_fields（generic_device）：行编辑 --- */
    const stateRows = ref([]);
    const sfSpec = props.schema.find(s => s.type === 'state_fields');
    if (sfSpec) {
      const existing = props.params[sfSpec.name] || {};
      stateRows.value = Object.entries(existing).map(([name, cfg]) => ({
        name, type: cfg.type || 'float', initial: cfg.initial ?? (cfg.type === 'bool' ? false : 0),
        min: cfg.min ?? '', max: cfg.max ?? '',
      }));
      if (!stateRows.value.length) {
        stateRows.value = [{ name: 'switch', type: 'bool', initial: false, min: '', max: '' }];
      }
      watch(stateRows, () => {
        const out = {};
        for (const r of stateRows.value) {
          if (!r.name) continue;
          const cfg = { type: r.type };
          if (r.type === 'bool') {
            cfg.initial = !!r.initial;
          } else {
            cfg.initial = +r.initial || 0;
            if (r.min !== '' && r.min !== null) cfg.min = +r.min;
            if (r.max !== '' && r.max !== null) cfg.max = +r.max;
          }
          out[r.name] = cfg;
        }
        props.params[sfSpec.name] = out;
      }, { deep: true, immediate: true });
    }
    function addStateRow() {
      stateRows.value.push({ name: '', type: 'float', initial: 0, min: '', max: '' });
    }

    function setScalar(spec, raw) {
      if (raw === '' || raw === null) { delete props.params[spec.name]; return; }
      if (spec.type === 'int' || spec.type === 'float') {
        const value = parseNumericInput(raw, spec.type === 'int');
        if (value === undefined) delete props.params[spec.name];
        else props.params[spec.name] = value;
        return;
      }
      props.params[spec.name] = raw;
    }

    return {
      jsonBufs, jsonErrs, onJson, wfEnabled, toggleWf,
      fieldRows, addFieldRow, stateRows, addStateRow, setScalar,
    };
  },
  template: `
  <div>
    <div class="form-grid" style="margin-bottom:10px">
      <template v-for="spec in schema" :key="spec.name">
        <label v-if="spec.type === 'int' || spec.type === 'float'" class="fl" :title="spec.help || ''">
          {{ spec.label }}{{ spec.required ? ' *' : '' }}
          <input type="number" :step="spec.type === 'int' ? 1 : 'any'"
                 :min="spec.min" :max="spec.max"
                 :value="params[spec.name]"
                 :placeholder="spec.default !== undefined ? String(spec.default) : ''"
                 @input="setScalar(spec, $event.target.value)">
        </label>
        <label v-else-if="spec.type === 'str'" class="fl" :title="spec.help || ''">
          {{ spec.label }}
          <input :value="params[spec.name]" @input="setScalar(spec, $event.target.value)">
        </label>
        <label v-else-if="spec.type === 'bool'" class="fl" :title="spec.help || ''">
          {{ spec.label }}
          <span><input type="checkbox" :checked="params[spec.name] ?? spec.default"
                       @change="params[spec.name] = $event.target.checked"></span>
        </label>
      </template>
    </div>

    <template v-for="spec in schema" :key="'c-' + spec.name">
      <!-- dict -->
      <div v-if="spec.type === 'dict'" style="margin-bottom:10px">
        <label class="fl">{{ spec.label }}（JSON）<span class="muted">{{ spec.help }}</span>
          <textarea rows="2" v-model="jsonBufs[spec.name]" @input="onJson(spec.name)"></textarea>
        </label>
        <div class="err-list" v-if="jsonErrs[spec.name]">{{ jsonErrs[spec.name] }}</div>
      </div>

      <!-- waveform_map -->
      <div v-if="spec.type === 'waveform_map'" style="margin-bottom:10px">
        <div style="font-size:13px;font-weight:600;margin-bottom:6px">
          {{ spec.label }} <span class="muted">{{ spec.help }}</span>
        </div>
        <div v-for="f in (spec.fields || [])" :key="f">
          <div style="margin:4px 0">
            <label>
              <input type="checkbox" :checked="wfEnabled(spec, f)"
                     @change="toggleWf(spec, f, $event.target.checked)">
              <span class="mono">{{ f }}</span>
              <span class="muted">{{ wfEnabled(spec, f) ? '自定义波形' : '使用默认波形' }}</span>
            </label>
          </div>
          <waveform-editor v-if="wfEnabled(spec, f)"
                           :field-name="f" :schemas="waveforms"
                           v-model="params[spec.name][f]"></waveform-editor>
        </div>
      </div>

      <!-- fields_map -->
      <div v-if="spec.type === 'fields_map'" style="margin-bottom:10px">
        <div style="font-size:13px;font-weight:600;margin-bottom:6px">
          {{ spec.label }} * <span class="muted">{{ spec.help }}</span>
        </div>
        <div v-for="(row, i) in fieldRows" :key="i" class="wf-editor">
          <div class="wf-head">
            <label class="fl">字段名 <input v-model="row.name" placeholder="如 ph" class="mono"></label>
            <label class="fl">精度 <input type="number" min="0" max="6" v-model.number="row.precision"></label>
            <label class="fl">单位（仅展示）<input v-model="row.unit" placeholder="如 ℃"></label>
            <button class="btn sm danger" style="margin-top:14px" @click="fieldRows.splice(i, 1)">删除</button>
          </div>
          <waveform-editor :schemas="waveforms" v-model="row.waveform"></waveform-editor>
        </div>
        <button class="btn sm" @click="addFieldRow">+ 添加字段</button>
      </div>

      <!-- state_fields -->
      <div v-if="spec.type === 'state_fields'" style="margin-bottom:10px">
        <div style="font-size:13px;font-weight:600;margin-bottom:6px">
          {{ spec.label }} * <span class="muted">{{ spec.help }}</span>
        </div>
        <table>
          <thead><tr><th>字段名</th><th>类型</th><th>初始值</th><th>min</th><th>max</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(row, i) in stateRows" :key="i">
              <td><input v-model="row.name" class="mono" style="width:130px"></td>
              <td>
                <select v-model="row.type">
                  <option value="float">float</option><option value="bool">bool</option>
                </select>
              </td>
              <td>
                <input v-if="row.type === 'bool'" type="checkbox" v-model="row.initial">
                <input v-else type="number" step="any" v-model.number="row.initial">
              </td>
              <td><input v-if="row.type === 'float'" type="number" step="any" v-model="row.min" style="width:80px"></td>
              <td><input v-if="row.type === 'float'" type="number" step="any" v-model="row.max" style="width:80px"></td>
              <td><button class="btn sm danger" @click="stateRows.splice(i, 1)">删除</button></td>
            </tr>
          </tbody>
        </table>
        <button class="btn sm" style="margin-top:6px" @click="addStateRow">+ 添加状态字段</button>
      </div>
    </template>
  </div>`
};

/* ============ 节点编辑弹层 ============ */

const NodeModal = {
  name: 'NodeModal',
  components: { ParamForm },
  props: { meta: Array, waveforms: Object, groupId: Number, editNode: Object },
  emits: ['close', 'saved'],
  setup(props, { emit }) {
    const isEdit = !!props.editNode;
    const module = ref(props.editNode?.module || (props.meta[0]?.module ?? ''));
    const nodeId = ref(props.editNode?.node_id || '');
    const enabled = ref(props.editNode ? props.editNode.enabled : true);
    const username = ref(props.editNode?.username || '');
    const password = ref(props.editNode?.password || '');
    const params = reactive(clone(props.editNode?.params || {}));
    const errors = ref([]);
    const warnings = ref([]);
    const saving = ref(false);
    const formKey = ref(0);

    const modMeta = computed(() => props.meta.find(m => m.module === module.value));

    watch(module, () => {
      // 切换模块时清空参数（编辑模式下首次不触发）
      for (const k of Object.keys(params)) delete params[k];
      errors.value = []; warnings.value = [];
      formKey.value++;
    });

    async function validate() {
      try {
        const r = await api('POST', '/api/nodes/validate',
          { module: module.value, node_id: nodeId.value, params: clone(params) });
        errors.value = r.errors; warnings.value = r.warnings;
        return r.ok;
      } catch (e) { errors.value = [e.message]; return false; }
    }

    async function save() {
      if (!nodeId.value) { errors.value = ['请填写节点 ID']; return; }
      saving.value = true;
      try {
        if (!(await validate())) return;
        const body = {
          module: module.value, node_id: nodeId.value, enabled: enabled.value,
          params: clone(params),
          username: username.value || null, password: password.value || null,
          sort_order: props.editNode?.sort_order || 0,
        };
        if (isEdit) await api('PUT', `/api/nodes/${props.editNode.id}`, body);
        else await api('POST', `/api/groups/${props.groupId}/nodes`, body);
        emit('saved');
      } catch (e) { errors.value = [e.message]; }
      finally { saving.value = false; }
    }

    return { isEdit, module, nodeId, enabled, username, password, params,
             errors, warnings, saving, modMeta, formKey, validate, save };
  },
  template: `
  <div class="modal-mask" @click.self="$emit('close')" @keydown.esc="$emit('close')">
    <div class="modal" role="dialog" aria-modal="true" :aria-label="isEdit ? '编辑节点' : '新建节点'">
      <div class="modal-head">
        <h3>{{ isEdit ? '编辑节点' : '新建节点' }}</h3>
      </div>
      <div class="modal-body">
        <div class="form-grid" style="margin-bottom:10px">
          <label class="fl">节点类型
            <select v-model="module" :disabled="isEdit">
              <option v-for="m in meta" :key="m.module" :value="m.module">
                {{ m.label }} [{{ m.node_type === 'sensor' ? '传感器' : '设备' }}]
              </option>
            </select>
          </label>
          <label class="fl">节点 ID（MQTT 中的 sensor_id / device_id）
            <input v-model="nodeId" class="mono" placeholder="如 GEN-PH-001">
          </label>
          <label class="fl">启用（渲染进 manifest）
            <span><input type="checkbox" v-model="enabled"></span>
          </label>
          <label class="fl">MQTT 账号（可选，覆盖 broker 级）
            <input v-model="username">
          </label>
          <label class="fl">MQTT 密码（可选）
            <input v-model="password" type="password">
          </label>
        </div>
        <div class="muted" v-if="modMeta" style="margin-bottom:8px">{{ modMeta.doc }}</div>

        <param-form v-if="modMeta" :key="formKey"
                    :schema="modMeta.params_schema" :params="params"
                    :waveforms="waveforms"></param-form>

        <div class="err-list" v-if="errors.length">{{ errors.join('\\n') }}</div>
        <div class="warn-list" v-if="warnings.length">{{ warnings.join('\\n') }}</div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="validate">校验</button>
        <button class="btn" @click="$emit('close')">取消</button>
        <button class="btn primary" :disabled="saving" @click="save">保存</button>
      </div>
    </div>
  </div>`
};

/* ============ 管理页 ============ */

const ManagePage = {
  name: 'ManagePage',
  components: { NodeModal },
  props: { meta: Array, waveforms: Object },
  emits: ['start-run', 'refresh-live'],
  setup(props, { emit }) {
    const groups = ref([]);
    const currentGid = ref(null);
    const nodes = ref([]);
    const brokers = ref([]);
    const showNodeModal = ref(false);
    const editNode = ref(null);
    const showGroupModal = ref(false);
    const groupForm = reactive({ id: null, name: '', description: '', broker_profile_id: null });
    const showImport = ref(false);
    const importText = ref('');
    const importName = ref('');
    const importReport = ref(null);
    const toast = (text, kind) => window.__toast(text, kind);

    const current = computed(() => groups.value.find(g => g.id === currentGid.value));
    const moduleLabel = (m) => props.meta.find(x => x.module === m)?.label || m;

    async function loadGroups(keep) {
      groups.value = await api('GET', '/api/groups');
      brokers.value = await api('GET', '/api/brokers');
      if (!keep || !groups.value.find(g => g.id === currentGid.value)) {
        currentGid.value = groups.value[0]?.id ?? null;
      }
      await loadNodes();
    }
    async function loadNodes() {
      nodes.value = currentGid.value
        ? await api('GET', `/api/groups/${currentGid.value}/nodes`) : [];
    }
    watch(currentGid, loadNodes);

    function openGroupModal(g) {
      Object.assign(groupForm, g
        ? { id: g.id, name: g.name, description: g.description, broker_profile_id: g.broker_profile_id }
        : { id: null, name: '', description: '', broker_profile_id: null });
      showGroupModal.value = true;
    }
    async function saveGroup() {
      try {
        const body = { name: groupForm.name, description: groupForm.description,
                       broker_profile_id: groupForm.broker_profile_id || null };
        if (groupForm.id) await api('PUT', `/api/groups/${groupForm.id}`, body);
        else {
          const g = await api('POST', '/api/groups', body);
          currentGid.value = g.id;
        }
        showGroupModal.value = false;
        await loadGroups(true);
        toast('分组已保存');
      } catch (e) { toast(e.message, 'error'); }
    }
    async function removeGroup(g) {
      if (!confirm(`删除分组 '${g.name}' 及其全部节点？`)) return;
      await api('DELETE', `/api/groups/${g.id}`);
      await loadGroups();
      toast('分组已删除');
    }

    async function removeNode(n) {
      if (!confirm(`删除节点 ${n.node_id}？`)) return;
      await api('DELETE', `/api/nodes/${n.id}`);
      await loadNodes(); await loadGroups(true);
    }
    async function duplicateNode(n) {
      try { await api('POST', `/api/nodes/${n.id}/duplicate`); await loadNodes(); await loadGroups(true); }
      catch (e) { toast(e.message, 'error'); }
    }
    async function toggleEnabled(n) {
      await api('PUT', `/api/nodes/${n.id}`, { ...n, enabled: !n.enabled });
      await loadNodes();
    }

    async function exportGroup() {
      const text = await api('GET', `/api/groups/${currentGid.value}/export`);
      const blob = new Blob([text], { type: 'text/yaml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${current.value?.name || 'manifest'}.yaml`;
      a.click();
    }
    async function doImport() {
      try {
        importReport.value = await api('POST', '/api/groups/import',
          { yaml_text: importText.value, group_name: importName.value || null });
        await loadGroups(true);
        currentGid.value = importReport.value.group.id;
      } catch (e) { toast(e.message, 'error'); }
    }
    function onImportFile(ev) {
      const f = ev.target.files[0];
      if (!f) return;
      const r = new FileReader();
      r.onload = () => { importText.value = r.result; };
      r.readAsText(f);
    }

    function startGroup() { emit('start-run', [currentGid.value]); }

    onMounted(loadGroups);
    return {
      groups, currentGid, current, nodes, brokers, moduleLabel,
      showNodeModal, editNode, showGroupModal, groupForm,
      showImport, importText, importName, importReport,
      loadGroups, loadNodes, openGroupModal, saveGroup, removeGroup,
      removeNode, duplicateNode, toggleEnabled, exportGroup, doImport, onImportFile, startGroup,
    };
  },
  template: `
  <div>
    <div class="manage-layout">
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <h3 style="margin:0">分组</h3>
          <div>
            <button class="btn sm" @click="showImport = true; importReport = null">导入</button>
            <button class="btn sm primary" @click="openGroupModal(null)">新建</button>
          </div>
        </div>
        <button v-for="g in groups" :key="g.id" type="button"
             :class="['group-item', { active: g.id === currentGid }]"
             @click="currentGid = g.id">
          <span>{{ g.name }}</span>
          <span class="cnt">{{ g.node_count }} 节点</span>
        </button>
        <div v-if="!groups.length" class="empty">还没有分组<br>点"新建"或"导入"开始</div>
      </div>

      <div class="card" v-if="current">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px">
          <div>
            <h3 style="margin:0;display:inline">{{ current.name }}</h3>
            <span class="muted" style="margin-left:8px">{{ current.description }}</span>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn sm primary" @click="startGroup">▶ 启动本组</button>
            <button class="btn sm" @click="editNode = null; showNodeModal = true">+ 新建节点</button>
            <button class="btn sm" @click="exportGroup">导出 YAML</button>
            <button class="btn sm" @click="openGroupModal(current)">编辑分组</button>
            <button class="btn sm danger" @click="removeGroup(current)">删除分组</button>
          </div>
        </div>
        <table>
          <thead><tr>
            <th>节点 ID</th><th>类型</th><th>启用</th><th>关键参数</th><th style="width:160px">操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="n in nodes" :key="n.id">
              <td class="mono">{{ n.node_id }}</td>
              <td>{{ moduleLabel(n.module) }}</td>
              <td>
                <button type="button" :class="['pill', n.enabled ? 'green' : 'gray']"
                        @click="toggleEnabled(n)">
                  {{ n.enabled ? '启用' : '禁用' }}
                </button>
              </td>
              <td>
                <div class="param-tags">
                  <span v-for="(v, k) in n.params" :key="k" class="param-tag" :title="k + ': ' + (typeof v === 'object' ? JSON.stringify(v) : v)">
                    <span class="p-key">{{ k }}</span>
                    <span class="p-val">{{ typeof v === 'object' ? '...' : v }}</span>
                  </span>
                  <span v-if="!Object.keys(n.params || {}).length" class="muted">—</span>
                </div>
              </td>
              <td>
                <button class="btn sm" @click="editNode = n; showNodeModal = true">编辑</button>
                <button class="btn sm" @click="duplicateNode(n)">复制</button>
                <button class="btn sm danger" @click="removeNode(n)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!nodes.length" class="empty">本组还没有节点</div>
      </div>
      <div class="card empty" v-else>选择或创建一个分组</div>
    </div>

    <node-modal v-if="showNodeModal"
                :meta="meta" :waveforms="waveforms"
                :group-id="currentGid" :edit-node="editNode"
                @close="showNodeModal = false"
                @saved="showNodeModal = false; loadNodes(); loadGroups(true)"></node-modal>

    <div class="modal-mask" v-if="showGroupModal" @click.self="showGroupModal = false" @keydown.esc="showGroupModal = false">
      <div class="modal" style="width:480px" role="dialog" aria-modal="true" aria-label="分组设置">
        <div class="modal-head">
          <h3>{{ groupForm.id ? '编辑分组' : '新建分组' }}</h3>
        </div>
        <div class="modal-body">
          <div style="display:flex;flex-direction:column;gap:10px">
            <label class="fl">名称 <input v-model="groupForm.name"></label>
            <label class="fl">描述 <input v-model="groupForm.description"></label>
            <label class="fl">Broker（不选则用默认）
              <select v-model="groupForm.broker_profile_id">
                <option :value="null">默认 broker</option>
                <option v-for="b in brokers" :key="b.id" :value="b.id">{{ b.name }}</option>
              </select>
            </label>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showGroupModal = false">取消</button>
          <button class="btn primary" @click="saveGroup">保存</button>
        </div>
      </div>
    </div>

    <div class="modal-mask" v-if="showImport" @click.self="showImport = false" @keydown.esc="showImport = false">
      <div class="modal" style="width:640px" role="dialog" aria-modal="true" aria-label="导入 manifest YAML">
        <div class="modal-head">
          <h3>导入 manifest YAML</h3>
        </div>
        <div class="modal-body">
          <div style="display:flex;flex-direction:column;gap:10px">
            <label class="fl">分组名（留空用 manifest 的 name）<input v-model="importName"></label>
            <input type="file" accept=".yaml,.yml" @change="onImportFile">
            <textarea rows="12" v-model="importText" placeholder="粘贴 manifest YAML 内容…"></textarea>
          </div>
          <div v-if="importReport" style="margin-top:10px">
            <div class="pill green">已导入 {{ importReport.imported }} 个节点</div>
            <div class="err-list" v-if="importReport.errors.length">{{ importReport.errors.join('\\n') }}</div>
            <div class="warn-list" v-if="importReport.warnings.length">{{ importReport.warnings.join('\\n') }}</div>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showImport = false">关闭</button>
          <button class="btn primary" @click="doImport">导入</button>
        </div>
      </div>
    </div>
  </div>`
};

/* ============ 监控页 ============ */

const MonitorPage = {
  name: 'MonitorPage',
  props: { live: Object, runs: Array, meta: Array, moduleMap: Object },
  emits: ['stop-run', 'send-command', 'refresh'],
  setup(props, { emit }) {
    const cmdModal = ref(null); // { node, command, args: {} }

    const runningRuns = computed(() => props.runs.filter(r => r.status === 'running'));
    const liveNodes = computed(() => Object.values(props.live.nodes)
      .sort((a, b) => (a.node_id > b.node_id ? 1 : -1)));

    function commandsOf(node) {
      const mod = props.moduleMap[`${node.node_type}:${node.node_id}`];
      const m = props.meta.find(x => x.module === mod);
      return m?.supported_commands || [];
    }
    function clickCommand(node, cmd) {
      if (cmd.args && cmd.args.length) {
        const args = {};
        for (const a of cmd.args) args[a.name] = a.choices ? a.choices[0] : (a.min ?? 0);
        cmdModal.value = { node, cmd, args };
      } else {
        emit('send-command', node, cmd.command, {});
      }
    }
    function submitCommand() {
      const { node, cmd, args } = cmdModal.value;
      const out = {};
      for (const a of cmd.args) {
        out[a.name] = (a.type === 'int') ? parseNumericInput(args[a.name], true)
          : (a.type === 'float') ? parseNumericInput(args[a.name]) : args[a.name];
      }
      emit('send-command', node, cmd.command, out);
      cmdModal.value = null;
    }
    function fmtAge(n) {
      if (n.age_s == null) return '—';
      return n.age_s < 60 ? `${Math.round(n.age_s)}s 前` : `${Math.round(n.age_s / 60)}min 前`;
    }
    function duration(r) {
      const start = new Date(r.started_at).getTime();
      const end = r.ended_at ? new Date(r.ended_at).getTime() : Date.now();
      const s = Math.max(0, Math.round((end - start) / 1000));
      return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`;
    }

    // 格式化单个数据值：小数保留至多3位有效小数，bool 显示汉字
    function fmtVal(v) {
      if (v == null) return '—';
      if (typeof v === 'boolean') return v ? '是' : '否';
      if (typeof v === 'number') {
        if (Number.isInteger(v)) return String(v);
        const s = v.toFixed(4);
        return parseFloat(s).toString(); // 去掉末尾0
      }
      return String(v);
    }

    // 过滤掉不用展示的内部字段
    const DATA_SKIP = new Set(['sensor_id', 'device_id', 'check_code', 'checkCode', 'timestamp']);
    function dataRows(n) {
      if (!n.last_data || typeof n.last_data !== 'object') return [];
      return Object.entries(n.last_data).filter(([k]) => !DATA_SKIP.has(k));
    }

    // 从 last_status 提取 enabled 状态（兼容驼峰和下划线命名）
    function statusEnabled(n) {
      const s = n.last_status;
      if (!s) return null;
      const v = s.isEnabled ?? s.is_enabled;
      return v === undefined ? null : Boolean(v);
    }

    // 提取心跳间隔（秒）
    function statusInterval(n) {
      const s = n.last_status;
      if (!s) return null;
      return s.statusReportInterval ?? s.status_report_interval ?? null;
    }

    return {
      cmdModal, runningRuns, liveNodes,
      commandsOf, clickCommand, submitCommand,
      fmtAge, duration, fmtVal, dataRows, statusEnabled, statusInterval,
    };
  },
  template: `
  <div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h3 style="margin:0">运行中的进程</h3>
        <button class="btn sm" @click="$emit('refresh')">刷新</button>
      </div>
      <table v-if="runningRuns.length" style="margin-top:8px">
        <thead><tr><th>Run</th><th>PID</th><th>分组</th><th>时长</th><th></th></tr></thead>
        <tbody>
          <tr v-for="r in runningRuns" :key="r.id">
            <td>#{{ r.id }}</td>
            <td class="mono">{{ r.pid }}</td>
            <td class="mono">{{ r.group_ids.join(', ') }}</td>
            <td>{{ duration(r) }}</td>
            <td><button class="btn sm danger" @click="$emit('stop-run', r.id)">停止</button></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">没有运行中的模拟进程 — 到"节点管理"页选择分组并启动</div>
    </div>

    <div class="card">
      <h3>节点实时状态 <span class="muted">（来自 MQTT status/data 订阅；命令为运行态控制，进程重启后恢复 DB 配置）</span></h3>
      <div class="live-grid">
        <div v-for="n in liveNodes" :key="n.node_type + ':' + n.node_id" class="live-card">
          <!-- 标题行：状态点 / ID / 类型 / 最后收包时间 -->
          <div class="head">
            <span :class="['dot', n.online ? 'on' : 'off']"></span>
            <span class="id" :title="n.node_id">{{ n.node_id }}</span>
            <span class="pill gray" style="flex-shrink:0">{{ n.node_type === 'sensor' ? '传感器' : '设备' }}</span>
            <span class="muted" style="margin-left:auto;flex-shrink:0;font-size:11px">{{ fmtAge(n) }}</span>
          </div>

          <!-- 数据字段：逐行展示，数值格式化，跳过内部字段 -->
          <div class="data-grid" v-if="dataRows(n).length">
            <template v-for="[k, v] in dataRows(n)" :key="k">
              <span class="dk">{{ k }}</span>
              <span class="dv" :title="String(v)">{{ fmtVal(v) }}</span>
            </template>
          </div>
          <div v-else-if="!n.last_data" class="muted" style="font-size:11px;margin:4px 0">等待数据…</div>

          <!-- 状态摘要行：enabled 标记 + 心跳间隔 + 最近事件 -->
          <div class="status-row" v-if="n.last_status || n.last_event">
            <template v-if="n.last_status">
              <span :class="['pill', statusEnabled(n) === false ? 'gray' : 'green']"
                    style="font-size:11px;padding:0 6px">
                {{ statusEnabled(n) === false ? '已停用' : '运行中' }}
              </span>
              <span class="muted" v-if="statusInterval(n)">心跳 {{ statusInterval(n) }}s</span>
            </template>
            <span class="evt muted" v-if="n.last_event" :title="n.last_event">
              ⟩ {{ n.last_event }}
            </span>
          </div>

          <!-- 命令按钮 -->
          <div class="cmds" v-if="commandsOf(n).length">
            <button v-for="c in commandsOf(n)" :key="c.command"
                    class="btn sm" @click="clickCommand(n, c)">{{ c.label }}</button>
          </div>
        </div>
      </div>
      <div v-if="!liveNodes.length" class="empty">还没有收到任何节点的 MQTT 消息</div>
    </div>

    <div class="modal-mask" v-if="cmdModal" @click.self="cmdModal = null" @keydown.esc="cmdModal = null">
      <div class="modal" style="width:400px" role="dialog" aria-modal="true" aria-label="发送节点命令">
        <div class="modal-head">
          <h3>{{ cmdModal.cmd.label }} → {{ cmdModal.node.node_id }}</h3>
        </div>
        <div class="modal-body">
          <div style="display:flex;flex-direction:column;gap:10px">
            <label v-for="a in cmdModal.cmd.args" :key="a.name" class="fl">
              {{ a.name }}<span v-if="a.min !== undefined"> ({{ a.min }}-{{ a.max }})</span>
              <select v-if="a.choices" v-model="cmdModal.args[a.name]">
                <option v-for="c in a.choices" :key="c" :value="c">{{ c }}</option>
              </select>
              <input v-else v-model="cmdModal.args[a.name]"
                     :type="a.type === 'str' ? 'text' : 'number'" step="any">
            </label>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="cmdModal = null">取消</button>
          <button class="btn primary" @click="submitCommand">发送</button>
        </div>
      </div>
    </div>
  </div>`
};

/* ============ 日志页 ============ */

const LogsPage = {
  name: 'LogsPage',
  props: { runs: Array, logEvents: Array },
  setup(props) {
    const runId = ref(null);
    const filter = ref('');
    const follow = ref(true);
    const history = ref([]);
    const boxEl = ref(null);

    watch(() => props.runs, () => {
      if (props.runs.length) {
        if (!props.runs.some(r => r.id === runId.value)) {
          runId.value = props.runs[0].id;
        }
      } else {
        runId.value = null;
      }
    }, { deep: true, immediate: true });

    async function loadHistory() {
      if (runId.value == null) { history.value = []; return; }
      const r = await api('GET', `/api/runs/${runId.value}/logs?offset=0&limit=2000`);
      history.value = r.lines;
      scrollBottom();
    }
    watch(runId, loadHistory, { immediate: true });

    const lines = computed(() => {
      if (runId.value == null) return [];
      const ws = props.logEvents
        .filter(e => e.run_id === runId.value)
        .map(e => e.line);
      const all = [...history.value, ...ws];
      const kw = filter.value.trim();
      return kw ? all.filter(l => l.includes(kw)) : all;
    });

    function scrollBottom() {
      nextTick(() => {
        if (follow.value && boxEl.value) boxEl.value.scrollTop = boxEl.value.scrollHeight;
      });
    }
    watch(lines, scrollBottom);

    function lineClass(l) {
      if (l.includes('ERROR') || l.includes('✗')) return 'err';
      if (l.includes('WARNING') || l.includes('⚠')) return 'warn';
      return '';
    }
    return { runId, filter, follow, lines, boxEl, lineClass, loadHistory };
  },
  template: `
  <div class="card">
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
      <h3 style="margin:0">运行日志</h3>
      <select v-model="runId" v-if="runs.length">
        <option v-for="r in runs" :key="r.id" :value="r.id">
          进程 #{{ r.id }} (运行中...)
        </option>
      </select>
      <span v-else class="muted">（暂无运行中的进程）</span>
      <input v-model="filter" placeholder="关键词过滤…" style="width:200px">
      <label><input type="checkbox" v-model="follow"> 自动滚动</label>
      <button class="btn sm" @click="loadHistory">重新加载</button>
    </div>
    <div class="log-box" ref="boxEl">
      <div v-for="(l, i) in lines" :key="i" :class="lineClass(l)">{{ l }}</div>
      <div v-if="!lines.length" style="opacity:.5">（暂无日志）</div>
    </div>
  </div>`
};

/* ============ 设置页 ============ */

const SettingsPage = {
  name: 'SettingsPage',
  emits: ['brokers-changed'],
  setup(props, { emit }) {
    const brokers = ref([]);
    const form = reactive({ id: null, name: '', host: '', port: 1883,
                            username: '', password: '', is_default: false });
    const showForm = ref(false);
    const testing = ref(0);
    const toast = (t, k) => window.__toast(t, k);

    async function load() { brokers.value = await api('GET', '/api/brokers'); }
    function openForm(b) {
      Object.assign(form, b
        ? { ...b, is_default: !!b.is_default }
        : { id: null, name: '', host: '', port: 1883, username: '', password: '', is_default: !brokers.value.length });
      showForm.value = true;
    }
    async function save() {
      try {
        const body = { name: form.name, host: form.host, port: +form.port,
                       username: form.username, password: form.password, is_default: form.is_default };
        if (form.id) await api('PUT', `/api/brokers/${form.id}`, body);
        else await api('POST', '/api/brokers', body);
        showForm.value = false;
        await load();
        emit('brokers-changed');
        toast('broker 已保存');
      } catch (e) { toast(e.message, 'error'); }
    }
    async function remove(b) {
      if (!confirm(`删除 broker '${b.name}'？`)) return;
      await api('DELETE', `/api/brokers/${b.id}`);
      await load();
    }
    async function test(b) {
      testing.value = b.id;
      try {
        const r = await api('POST', `/api/brokers/${b.id}/test`);
        toast(r.ok ? `✓ ${b.host} 连接成功` : `✗ 连接失败: ${r.error || '未知错误'}`, r.ok ? 'ok' : 'error');
      } catch (e) { toast(e.message, 'error'); }
      finally { testing.value = 0; }
    }
    async function importConfig() {
      try {
        const b = await api('POST', '/api/brokers/import-config');
        toast(`已从 config.yaml 导入: ${b.host}`);
        await load();
        emit('brokers-changed');
      } catch (e) { toast(e.message, 'error'); }
    }

    onMounted(load);
    return { brokers, form, showForm, testing, openForm, save, remove, test, importConfig };
  },
  template: `
  <div class="card settings-card">
    <div class="settings-card-head">
      <h3 style="margin:0">Broker 配置</h3>
      <div class="settings-card-actions">
        <button class="btn sm" @click="importConfig">导入 config.yaml</button>
        <button class="btn sm primary" @click="openForm(null)">新建</button>
      </div>
    </div>
    <table>
      <thead><tr><th>名称</th><th>地址</th><th>账号</th><th>默认</th><th style="width:200px">操作</th></tr></thead>
      <tbody>
        <tr v-for="b in brokers" :key="b.id">
          <td>{{ b.name }}</td>
          <td class="mono">{{ b.host }}:{{ b.port }}</td>
          <td class="mono">{{ b.username || '（匿名）' }}</td>
          <td><span v-if="b.is_default" class="pill">默认</span></td>
          <td>
            <button class="btn sm" :disabled="testing === b.id" @click="test(b)">
              {{ testing === b.id ? '测试中…' : '测试连接' }}
            </button>
            <button class="btn sm" @click="openForm(b)">编辑</button>
            <button class="btn sm danger" @click="remove(b)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="!brokers.length" class="empty">
      还没有 broker —— 新建一个，或从 simulation/config.yaml 导入
    </div>

    <teleport to="body">
      <div class="modal-mask" v-if="showForm" @click.self="showForm = false" @keydown.esc="showForm = false">
        <div class="modal broker-modal" role="dialog" aria-modal="true" aria-label="Broker 设置">
          <div class="modal-head">
            <h3>{{ form.id ? '编辑' : '新建' }} broker</h3>
          </div>
          <div class="modal-body">
            <div style="display:flex;flex-direction:column;gap:10px">
              <label class="fl">名称 <input v-model="form.name" placeholder="如 本地 EMQX"></label>
              <label class="fl">主机 <input v-model="form.host" class="mono" placeholder="127.0.0.1"></label>
              <label class="fl">端口 <input type="number" v-model="form.port"></label>
              <label class="fl">账号（可选）<input v-model="form.username"></label>
              <label class="fl">密码（可选）<input v-model="form.password" type="password"></label>
              <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-2);margin-top:4px">
                <input type="checkbox" v-model="form.is_default" style="margin:0"> 设为默认（monitor 与未绑定分组使用）
              </label>
            </div>
          </div>
          <div class="modal-foot">
            <button class="btn" @click="showForm = false">取消</button>
            <button class="btn primary" @click="save">保存</button>
          </div>
        </div>
      </div>
    </teleport>
  </div>`
};

/* ============ 根应用 ============ */

createApp({
  components: { ManagePage, MonitorPage, LogsPage, SettingsPage },
  setup() {
    const tabs = [
      { key: 'manage', label: '节点管理', description: '编排分组、节点参数与波形，然后以独立进程启动仿真。' },
      { key: 'monitor', label: '运行监控', description: '查看 MQTT 实时状态、遥测数据，并向在线节点发送控制命令。' },
      { key: 'logs', label: '运行日志', description: '跟随进程输出，快速定位连接、校验与命令处理问题。' },
      { key: 'settings', label: '连接设置', description: '管理 Broker 连接资料与模拟器默认通信入口。' },
    ];
    const tab = ref('manage');
    const currentTab = computed(() => tabs.find(item => item.key === tab.value) || tabs[0]);
    const meta = ref([]);
    const waveformSchemas = ref({});
    const runs = ref([]);
    const live = reactive({ nodes: {} });
    const moduleMap = reactive({});
    const monitorConnected = ref(false);
    const wsStatus = ref('connecting');
    const toasts = ref([]);
    const isSwitchingView = ref(false);
    let toastSeq = 0;
    let activeViewTransition = null;
    let fallbackMotionTimer = null;

    const wsStatusText = computed(() => ({
      connecting: '连接中', open: '已连接', closed: '已断开',
    }[wsStatus.value] || wsStatus.value));

    function toast(text, kind = 'ok') {
      const id = ++toastSeq;
      toasts.value.push({ id, text, kind });
      setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id); }, 4000);
    }
    window.__toast = toast;

    async function switchTab(nextTab) {
      if (nextTab === tab.value || !tabs.some(item => item.key === nextTab)) return;

      const updateTab = async () => {
        tab.value = nextTab;
        await nextTick();
      };
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduceMotion) {
        await updateTab();
        return;
      }
      if (!document.startViewTransition) {
        clearTimeout(fallbackMotionTimer);
        isSwitchingView.value = false;
        await nextTick();
        await updateTab();
        await new Promise(resolve => requestAnimationFrame(resolve));
        isSwitchingView.value = true;
        fallbackMotionTimer = setTimeout(() => {
          isSwitchingView.value = false;
        }, 200);
        return;
      }

      activeViewTransition?.skipTransition();
      const transition = document.startViewTransition(updateTab);
      activeViewTransition = transition;
      transition.finished.finally(() => {
        if (activeViewTransition === transition) activeViewTransition = null;
      });
      await transition.updateCallbackDone;
    }

    /* --- 日志事件（环形缓冲） --- */
    const logEvents = ref([]);
    function pushLog(e) {
      logEvents.value.push(e);
      if (logEvents.value.length > 3000) logEvents.value.splice(0, 1000);
    }

    /* --- WebSocket --- */
    let ws = null, retry = 1000, pingTimer = null;
    function connectWs() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      wsStatus.value = 'connecting';
      ws.onopen = () => {
        wsStatus.value = 'open'; retry = 1000;
        pingTimer = setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 20000);
      };
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.type === 'log_line') pushLog(msg);
        else if (msg.type === 'run_state') refreshRuns();
        else if (msg.type === 'node_status' || msg.type === 'node_data') {
          const n = msg.node;
          live.nodes[`${n.node_type}:${n.node_id}`] = n;
        } else if (msg.type === 'monitor_state') {
          monitorConnected.value = msg.connected;
        }
      };
      ws.onclose = () => {
        wsStatus.value = 'closed';
        clearInterval(pingTimer);
        setTimeout(connectWs, retry);
        retry = Math.min(retry * 2, 15000);
      };
    }

    /* --- 数据加载 --- */
    async function refreshRuns() { runs.value = await api('GET', '/api/runs'); }
    async function refreshLive() {
      const r = await api('GET', '/api/live/nodes');
      monitorConnected.value = r.connected;
      for (const n of r.nodes) live.nodes[`${n.node_type}:${n.node_id}`] = n;
    }
    async function refreshModuleMap() {
      // node_id -> module 映射（监控页据此渲染该节点支持的命令按钮）
      const typeOf = {};
      for (const m of meta.value) typeOf[m.module] = m.node_type;
      const groups = await api('GET', '/api/groups');
      for (const g of groups) {
        const nodes = await api('GET', `/api/groups/${g.id}/nodes`);
        for (const n of nodes) {
          const t = typeOf[n.module];
          if (t) moduleMap[`${t}:${n.node_id}`] = n.module;
        }
      }
    }
    async function refreshAll() {
      await Promise.all([refreshRuns(), refreshLive(), refreshModuleMap()]);
    }

    /* --- 操作 --- */
    async function startRun(groupIds) {
      try {
        const r = await api('POST', '/api/runs', { group_ids: groupIds });
        toast(`run #${r.id} 已启动 (pid ${r.pid})`);
        await switchTab('monitor');
        await refreshAll();
      } catch (e) { toast(e.message, 'error'); }
    }
    async function stopRun(runId) {
      try {
        await api('POST', `/api/runs/${runId}/stop`);
        toast(`run #${runId} 停止指令已发送`);
      } catch (e) { toast(e.message, 'error'); }
    }
    async function sendCommand(node, command, args) {
      try {
        await api('POST', `/api/live/${node.node_type}/${node.node_id}/command`,
          { command, args });
        toast(`已发送 ${command} → ${node.node_id}`);
      } catch (e) { toast(e.message, 'error'); }
    }

    onMounted(async () => {
      meta.value = await api('GET', '/api/meta/modules');
      waveformSchemas.value = await api('GET', '/api/meta/waveforms');
      await refreshAll();
      connectWs();
    });
    onBeforeUnmount(() => {
      clearTimeout(fallbackMotionTimer);
      ws && ws.close();
    });

    return {
      tabs, tab, currentTab, meta, waveformSchemas, runs, live, moduleMap,
      monitorConnected, wsStatus, wsStatusText, toasts, logEvents, isSwitchingView,
      switchTab, startRun, stopRun, sendCommand, refreshLive, refreshAll,
    };
  },
}).mount('#app');
