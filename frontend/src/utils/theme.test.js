import test from 'node:test'
import assert from 'node:assert/strict'

import { DEFAULT_COLOR_THEME, normalizeColorTheme } from './theme.js'

test('旧 Claude 配色值迁移为 Apple', () => {
  assert.equal(normalizeColorTheme('claude'), 'apple')
})

test('只接受受支持的配色值', () => {
  assert.equal(normalizeColorTheme('classic'), 'classic')
  assert.equal(normalizeColorTheme('apple'), 'apple')
  assert.equal(normalizeColorTheme('unknown'), DEFAULT_COLOR_THEME)
  assert.equal(normalizeColorTheme(null), DEFAULT_COLOR_THEME)
})
