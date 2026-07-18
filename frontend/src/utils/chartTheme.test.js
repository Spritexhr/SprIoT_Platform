import test from 'node:test'
import assert from 'node:assert/strict'

import { withAlpha } from './chartTheme.js'

test('图表主题色支持三位与六位 hex 透明度转换', () => {
  assert.equal(withAlpha('#abc', 0.25), 'rgba(170, 187, 204, 0.25)')
  assert.equal(withAlpha('#d96845', 0.14), 'rgba(217, 104, 69, 0.14)')
})

test('图表主题色支持 rgb，并保留无法解析的 CSS 颜色', () => {
  assert.equal(withAlpha('rgb(39, 134, 95)', 0.5), 'rgba(39, 134, 95, 0.5)')
  assert.equal(withAlpha('currentColor', 0.5), 'currentColor')
})
