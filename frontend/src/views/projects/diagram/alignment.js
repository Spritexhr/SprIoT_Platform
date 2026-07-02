const VERTICAL_HANDLES = new Set(['top', 'bottom'])
const HORIZONTAL_HANDLES = new Set(['left', 'right'])

function handleCenter(node, handleId) {
  const position = node?.position || { x: 0, y: 0 }
  const width = Number(node?.data?.size?.w || node?.dimensions?.width || 0)
  const height = Number(node?.data?.size?.h || node?.dimensions?.height || 0)
  switch (handleId) {
    case 'left':   return { x: position.x, y: position.y + height / 2 }
    case 'right':  return { x: position.x + width, y: position.y + height / 2 }
    case 'top':    return { x: position.x + width / 2, y: position.y }
    case 'bottom': return { x: position.x + width / 2, y: position.y + height }
    default:       return null
  }
}

/**
 * 将当前拖动节点的连接点吸附到附近的直接相连节点。
 * 只返回 movableNodeId 的新坐标，其他节点永远不会被带动。
 */
export function computeNearAlignedPositions({
  nodes = [], edges = [], threshold = 10, movableNodeId = null,
}) {
  if (!movableNodeId) return new Map()
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const movable = byId.get(movableNodeId)
  if (!movable) return new Map()
  const position = { ...movable.position }

  for (const axis of ['x', 'y']) {
    const deltas = []
    for (const edge of edges) {
      const isSource = edge.source === movableNodeId
      const isTarget = edge.target === movableNodeId
      if (!isSource && !isTarget) continue

      const neighbor = byId.get(isSource ? edge.target : edge.source)
      if (!neighbor) continue
      const movableHandle = isSource
        ? (edge.sourceHandle || 'right')
        : (edge.targetHandle || 'left')
      const neighborHandle = isSource
        ? (edge.targetHandle || 'left')
        : (edge.sourceHandle || 'right')
      const compatible = axis === 'x'
        ? VERTICAL_HANDLES.has(movableHandle) && VERTICAL_HANDLES.has(neighborHandle)
        : HORIZONTAL_HANDLES.has(movableHandle) && HORIZONTAL_HANDLES.has(neighborHandle)
      if (!compatible) continue

      const movablePoint = handleCenter({ ...movable, position }, movableHandle)
      const neighborPoint = handleCenter(neighbor, neighborHandle)
      const delta = neighborPoint[axis] - movablePoint[axis]
      if (Math.abs(delta) <= threshold) deltas.push(delta)
    }
    if (!deltas.length) continue
    const averageDelta = deltas.reduce((sum, value) => sum + value, 0) / deltas.length
    position[axis] = Math.round((position[axis] + averageDelta) * 2) / 2
  }

  const moved = Math.abs(position.x - movable.position.x) >= 0.01
    || Math.abs(position.y - movable.position.y) >= 0.01
  return moved ? new Map([[movableNodeId, position]]) : new Map()
}
