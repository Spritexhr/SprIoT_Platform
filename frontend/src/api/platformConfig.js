import request from './index'

/** 获取平台配置列表 */
export function getPlatformConfigs(params = {}) {
  return request.get('/platform-configs/', { params })
}

/** 获取单条配置（按 key） */
export function getPlatformConfig(key) {
  return request.get(`/platform-configs/${encodeURIComponent(key)}/`)
}

/** 创建配置（仅管理员） */
export function createPlatformConfig(data) {
  return request.post('/platform-configs/', data)
}

/** 更新配置（仅管理员） */
export function updatePlatformConfig(key, data) {
  return request.put(`/platform-configs/${encodeURIComponent(key)}/`, data)
}

/** 删除配置（仅管理员） */
export function deletePlatformConfig(key) {
  return request.delete(`/platform-configs/${encodeURIComponent(key)}/`)
}

/** 使配置生效（MQTT 重连等，仅管理员） */
export function reloadPlatformConfig() {
  return request.post('/platform-configs/reload/')
}

/**
 * 预览或执行历史数据清理（仅管理员）。
 * 默认始终为试运行，实际删除还需由调用方明确传入 dry_run: false 和确认令牌。
 */
export function runCleanupOldData(payload = {}) {
  return request.post('/platform-configs/cleanup-old-data/', {
    dry_run: true,
    ...payload,
  })
}

/** 获取平台预定义配置项 schema（来源 defaults.py） */
export function getConfigSchema() {
  return request.get('/platform-configs/schema/')
}

/** 测试 MQTT 连接（可传入临时 broker/port/username/password） */
export function testMqttConnection(payload = {}) {
  return request.post('/platform-configs/test-mqtt/', payload)
}
