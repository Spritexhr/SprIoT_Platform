import assert from 'node:assert/strict'
import test from 'node:test'

import { computeNearAlignedPositions } from './alignment.js'

function node(id, x, width = 100) {
  return { id, position: { x, y: 0 }, data: { size: { w: width, h: 80 } } }
}

test('拖动节点按连接点中心吸附，不移动其他节点', () => {
  const nodes = [node('sensor', 100, 100), node('control', 100, 110), node('device', 100, 100)]
  const edges = [
    { source: 'sensor', target: 'control', sourceHandle: 'bottom', targetHandle: 'top' },
    { source: 'control', target: 'device', sourceHandle: 'bottom', targetHandle: 'top' },
  ]
  const changed = computeNearAlignedPositions({ nodes, edges, movableNodeId: 'control' })

  assert.deepEqual([...changed.keys()], ['control'])
  assert.deepEqual(changed.get('control'), { x: 95, y: 0 })
})

test('对齐结果不受连线数组顺序影响', () => {
  const nodes = [node('a', 100), node('b', 104), node('c', 108)]
  const edges = [
    { source: 'a', target: 'b', sourceHandle: 'bottom', targetHandle: 'top' },
    { source: 'b', target: 'c', sourceHandle: 'bottom', targetHandle: 'top' },
  ]
  const forward = computeNearAlignedPositions({ nodes, edges, movableNodeId: 'b' })
  const reversed = computeNearAlignedPositions({ nodes, edges: [...edges].reverse(), movableNodeId: 'b' })

  assert.deepEqual([...forward], [...reversed])
})

test('节点初始化或新增无关图元时不执行位置校正', () => {
  const nodes = [node('a', 100), node('b', 106), node('new', 110)]
  const edges = [{ source: 'a', target: 'b', sourceHandle: 'bottom', targetHandle: 'top' }]

  assert.equal(computeNearAlignedPositions({ nodes, edges }).size, 0)
})

test('超过阈值的偏差不会被自动移动', () => {
  const nodes = [node('a', 0), node('b', 20)]
  const edges = [{ source: 'a', target: 'b', sourceHandle: 'bottom', targetHandle: 'top' }]

  assert.equal(computeNearAlignedPositions({ nodes, edges, threshold: 10, movableNodeId: 'a' }).size, 0)
})
