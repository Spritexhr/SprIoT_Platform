import request from './index'

// ==================== 自动化规则 ====================

/** 获取规则列表（支持筛选） */
export function getAutomationRules(params = {}) {
  return request.get('/automation-rules/', { params })
}

/** 获取规则详情（含脚本内容） */
export function getAutomationRule(id) {
  return request.get(`/automation-rules/${id}/`)
}

/** 创建规则 */
export function createAutomationRule(data) {
  return request.post('/automation-rules/', data)
}

/** 更新规则 */
export function updateAutomationRule(id, data) {
  return request.put(`/automation-rules/${id}/`, data)
}

/** 删除规则 */
export function deleteAutomationRule(id) {
  return request.delete(`/automation-rules/${id}/`)
}

/** 批量移动自动化规则到文件夹；folder 为 null 时移到未分类。 */
export function bulkMoveAutomationRules(ruleIds, folder) {
  return request.post('/automation-rules/bulk-move/', { rule_ids: ruleIds, folder })
}

/** 批量更新自动化规则显示顺序，order 是规则 id 数组。 */
export function reorderAutomationRules(order, context = {}) {
  return request.post('/automation-rules/reorder/', { order, ...context })
}

/** 手动执行规则 */
export function executeAutomationRule(id) {
  return request.post(`/automation-rules/${id}/execute/`)
}

/** 启动轮询（可附带轮询间隔） */
export function launchAutomationRule(id, pollInterval) {
  const data = {}
  if (pollInterval !== undefined) data.poll_interval = pollInterval
  return request.post(`/automation-rules/${id}/launch/`, data)
}

/** 停止轮询 */
export function stopAutomationRule(id, reason = 'user', errorMessage = '') {
  return request.post(`/automation-rules/${id}/stop/`, {
    reason,
    error_message: errorMessage,
  })
}

/** 获取可关联资源；传 project + section 时仅返回该房间已导入成员 */
export function getAvailableSources(params = {}) {
  return request.get('/automation-rules/available-sources/', { params })
}
