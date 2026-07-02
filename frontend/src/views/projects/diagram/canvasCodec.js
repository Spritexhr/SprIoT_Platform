import { getPidEdgeStyle, normalizeEdgeData } from './edgeTypes.js'
import { serializedNodeSize } from './nodeGeometry.js'

export function emptyCanvas() {
  return { version: 1, viewport: { x: 0, y: 0, zoom: 1 }, nodes: [], edges: [] }
}

/** 持久化 canvas -> Vue Flow，编辑态和预览态共用这一个入口。 */
export function canvasToFlow(canvas) {
  const source = canvas && typeof canvas === 'object' ? canvas : emptyCanvas()
  const nodes = (source.nodes || []).map((node) => ({
    id: node.id,
    type: node.type,
    position: { x: node.position?.x ?? 0, y: node.position?.y ?? 0 },
    data: {
      ...(node.data || {}),
      binding: node.binding || { kind: 'none', id: '' },
      size: node.size,
    },
  }))
  const edges = (source.edges || []).map((edge) => {
    const data = normalizeEdgeData(edge.data)
    return {
      id: edge.id,
      source: edge.source,
      sourceHandle: edge.sourcePort || edge.sourceHandle || 'right',
      target: edge.target,
      targetHandle: edge.targetPort || edge.targetHandle || 'left',
      type: 'pid',
      data,
      label: data.label,
      style: getPidEdgeStyle(data.kind),
      markerEnd: 'arrowclosed',
    }
  })
  return { nodes, edges }
}

function stripRuntimeData(data) {
  if (!data) return {}
  const { binding, size, ...rest } = data
  return rest
}

/** Vue Flow -> 持久化 canvas，只保留可重建的业务数据与几何数据。 */
export function flowToCanvas({ nodes = [], edges = [], viewport }) {
  return {
    version: 1,
    viewport: viewport || { x: 0, y: 0, zoom: 1 },
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.type,
      // 保留 0.5px 精度，奇数尺寸节点的中心线才能稳定重建。
      position: {
        x: Math.round(node.position.x * 2) / 2,
        y: Math.round(node.position.y * 2) / 2,
      },
      // 尺寸由 Vue Flow 内部测量，只在序列化时读取；
      // 不再为了存尺寸而替换运行中的节点对象。
      size: serializedNodeSize(node),
      binding: node.data?.binding || { kind: 'none', id: '' },
      data: stripRuntimeData(node.data),
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      sourcePort: edge.sourceHandle || 'right',
      target: edge.target,
      targetPort: edge.targetHandle || 'left',
      type: 'process_line',
      data: normalizeEdgeData(edge.data),
    })),
  }
}
