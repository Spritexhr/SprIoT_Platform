<template>
  <div class="editor">
    <ToolboxPanel />

    <div class="editor__canvas-wrap" @drop="onDrop" @dragover.prevent>
      <VueFlow
        v-model:nodes="nodes"
        v-model:edges="edges"
        :node-types="nodeTypes"
        :edge-types="edgeTypes"
        :default-viewport="defaultViewport"
        :snap-to-grid="true"
        :snap-grid="[10, 10]"
        :delete-key-code="['Backspace', 'Delete']"
        :connection-mode="ConnectionMode.Loose"
        @nodes-change="onNodesChange"
        @node-drag-stop="onNodeDragStop"
        @edges-change="onEdgesChange"
        @connect="onConnect"
        @node-click="onNodeClick"
        @edge-click="onEdgeClick"
        @pane-click="clearSelection"
        @viewport-change-end="onViewportChangeEnd"
      >
        <Background pattern-color="#d8d2c4" :gap="16" />
        <Controls />
      </VueFlow>
    </div>

    <PropertiesPanel
      :selection="selection"
      :targets="targets"
      @update-node="applyNodePatch"
      @update-edge="applyEdgePatch"
      @delete-node="removeNode"
      @delete-edge="removeEdge"
    />
  </div>
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'
import { ConnectionMode, VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'

import ToolboxPanel from './ToolboxPanel.vue'
import PropertiesPanel from './PropertiesPanel.vue'
import { buildNodeTypes } from './nodeTypes'
import { buildEdgeTypes, getPidEdgeStyle, normalizeEdgeData } from '../edgeTypes'
import { canvasToFlow, flowToCanvas as encodeCanvas } from '../canvasCodec'
import { computeNearAlignedPositions } from '../alignment'

const props = defineProps({
  diagram: { type: Object, required: true },
  targets: { type: Object, default: () => ({ sensors: [], devices: [] }) },
})
const emit = defineEmits(['change'])

// 节点类型映射（type -> 组件），由图元注册表自动生成
const nodeTypes = buildNodeTypes()
const edgeTypes = buildEdgeTypes()

const { project, screenToFlowCoordinate, updateNode, updateNodeInternals, setEdges } = useVueFlow()

function flowToCanvas() {
  return encodeCanvas({ nodes: nodes.value, edges: edges.value, viewport: viewport.value })
}

// ============ state ============
const initial = canvasToFlow(props.diagram?.canvas)
const nodes = ref(initial.nodes)
const edges = ref(initial.edges)
const viewport = ref(props.diagram?.canvas?.viewport || { x: 0, y: 0, zoom: 1 })
const defaultViewport = viewport.value

const selection = ref(null)        // { kind: 'node'|'edge', payload }

// 当父组件换了 diagram（例如从列表跳转过来），重置画布
watch(() => props.diagram?.id, () => {
  const init = canvasToFlow(props.diagram?.canvas)
  nodes.value = init.nodes
  edges.value = init.edges
  viewport.value = props.diagram?.canvas?.viewport || { x: 0, y: 0, zoom: 1 }
  selection.value = null
})

// ============ 事件 ============
function emitCanvas() {
  emit('change', flowToCanvas())
}
function onEdgesChange(changes = []) {
  if (changes.some((change) => change.type !== 'select')) emitCanvas()
}

function onNodesChange(changes = []) {
  let shouldPersist = false
  for (const change of changes) {
    if (change.type === 'remove' || change.type === 'add' || change.type === 'reset') {
      shouldPersist = true
    } else if (change.type === 'position' && change.dragging === false) {
      // 键盘方向键移动没有 node-drag-stop，需在最终 position change 落盘。
      shouldPersist = true
    }
  }
  if (shouldPersist) emitCanvas()
}
function onViewportChangeEnd(v) {
  viewport.value = v
  emitCanvas()
}

function onConnect(connection) {
  const id = `e_${Date.now()}_${Math.floor(Math.random() * 1000)}`
  const data = normalizeEdgeData()
  edges.value.push({
    id,
    source: connection.source,
    sourceHandle: connection.sourceHandle || 'right',
    target: connection.target,
    targetHandle: connection.targetHandle || 'left',
    type: 'pid',
    data,
    label: data.label,
    style: getPidEdgeStyle(data.kind),
    markerEnd: 'arrowclosed',
  })
  emitCanvas()
}

function onNodeClick({ node }) {
  selection.value = { kind: 'node', payload: node }
}
function onEdgeClick({ edge }) {
  selection.value = { kind: 'edge', payload: edge }
}
function clearSelection() {
  selection.value = null
}

// ============ 连接点近对齐自动校正 ============
// Vue Flow 的网格吸附对齐的是节点左上角，不是连接点中心。节点尺寸不同时，
// 即使看起来已对齐，线端仍可能差几像素，SmoothStepEdge 会把它画成小折皱。
const HANDLE_ALIGN_THRESHOLD = 10
async function normalizeNearAlignedEdges(movableNodeId) {
  const changed = computeNearAlignedPositions({
    nodes: nodes.value,
    edges: edges.value,
    threshold: HANDLE_ALIGN_THRESHOLD,
    movableNodeId,
  })
  if (!changed.size) return false
  for (const [nodeId, position] of changed) {
    const index = nodes.value.findIndex((item) => item.id === nodeId)
    nodes.value[index] = { ...nodes.value[index], position }
    updateNode(nodeId, { position, data: nodes.value[index].data })
  }
  await nextTick()
  updateNodeInternals([...changed.keys()])
  return true
}

async function onNodeDragStop({ node }) {
  await normalizeNearAlignedEdges(node?.id)
  // 无论是否发生自动对齐，拖动结束都是一次完整的持久化事件。
  emitCanvas()
}

function applyNodePatch({ id, patch }) {
  const idx = nodes.value.findIndex((n) => n.id === id)
  if (idx < 0) return
  const newData = { ...nodes.value[idx].data, ...patch.data }
  const newPos = patch.position ?? nodes.value[idx].position
  const nextNode = { ...nodes.value[idx], data: newData, position: newPos }
  // 先写 v-model 数据源，再更新 Vue Flow 内部 store。这样属性面板连续输入时
  // selection 不会拿到旧节点反向覆盖表单，保存时也能直接读到最新参数。
  nodes.value[idx] = nextNode
  updateNode(id, { data: newData, position: newPos })
  // 旋转/镜像后连接点的实际渲染位置变了，得让 Vue Flow 重新量一下，
  // 否则连线还接在节点变换前的旧位置上
  updateNodeInternals([id])
  if (selection.value?.kind === 'node' && selection.value.payload.id === id) {
    selection.value = { kind: 'node', payload: nextNode }
  }
  emitCanvas()
}

function applyEdgePatch({ id, patch }) {
  const idx = edges.value.findIndex((e) => e.id === id)
  if (idx < 0) return
  const data = normalizeEdgeData({ ...edges.value[idx].data, ...patch.data })
  const nextEdges = edges.value.map((edge) => edge.id === id ? {
    ...edge,
    data,
    label: data.label,
    style: getPidEdgeStyle(data.kind),
  } : edge)
  // 与节点修改一样，先写 v-model 数据源，再写 Vue Flow 内部 store。
  // 否则紧接着的 emitCanvas 可能仍序列化旧连线。
  edges.value = nextEdges
  setEdges(nextEdges)
  if (selection.value?.kind === 'edge' && selection.value.payload.id === id) {
    selection.value = { kind: 'edge', payload: nextEdges[idx] }
  }
  emitCanvas()
}

function removeNode(id) {
  nodes.value = nodes.value.filter((n) => n.id !== id)
  edges.value = edges.value.filter((e) => e.source !== id && e.target !== id)
  selection.value = null
  emitCanvas()
}
function removeEdge(id) {
  edges.value = edges.value.filter((e) => e.id !== id)
  selection.value = null
  emitCanvas()
}

// 从工具箱拖入新节点
function onDrop(event) {
  event.preventDefault()
  const raw = event.dataTransfer?.getData('application/x-plant-node')
  if (!raw) return
  let payload
  try { payload = JSON.parse(raw) } catch { return }

  // 屏幕坐标 → 画布坐标
  const pos = screenToFlowCoordinate
    ? screenToFlowCoordinate({ x: event.clientX, y: event.clientY })
    : project({ x: event.clientX, y: event.clientY })

  const id = `n_${payload.type}_${Date.now()}_${Math.floor(Math.random() * 1000)}`
  const newNode = {
    id,
    type: payload.type,
    position: { x: Math.round(pos.x), y: Math.round(pos.y) },
    data: {
      ...(payload.defaultData || {}),
      binding: { kind: 'none', id: '' },
    },
  }
  // 使用新数组递交给 Vue Flow，避免原地 push 与尺寸测量的
  // 内部同步交错，把上一个新增节点的旧坐标写回。
  nodes.value = [...nodes.value, newNode]
  emitCanvas()
}
</script>

<style scoped>
.editor {
  flex: 1;
  display: flex;
  min-height: 0;
}

.editor__canvas-wrap {
  flex: 1;
  position: relative;
  background: #f4f3ee;
}

:deep(.vue-flow__node) {
  font-family: -apple-system, 'PingFang SC', sans-serif;
}

:deep(.vue-flow__edge-text) {
  font-size: 11px;
  fill: #555;
}
</style>
