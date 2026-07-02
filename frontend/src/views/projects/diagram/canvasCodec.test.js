import assert from 'node:assert/strict'
import test from 'node:test'

import { serializedNodeSize } from './nodeGeometry.js'

test('保存时从 Vue Flow dimensions 读取尺寸且不改写节点', () => {
  const node = {
    id: 'new-node',
    type: 'valve',
    position: { x: 120, y: 80 },
    dimensions: { width: 56, height: 35 },
    data: { label: 'FCV', binding: { kind: 'none', id: '' } },
  }
  const positionBefore = { ...node.position }

  const size = serializedNodeSize(node)

  assert.deepEqual(size, { w: 56, h: 35 })
  assert.deepEqual(node.position, positionBefore)
  assert.equal(node.data.size, undefined)
})
