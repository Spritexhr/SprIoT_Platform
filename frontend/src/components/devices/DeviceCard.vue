<template>
  <div class="device-card iot-card iot-card--hover" @click="$emit('click', device)">
    <!-- 顶部：状态 + 类型名 -->
    <div class="device-card__header">
      <div class="device-card__title">
        <span
          class="iot-status-dot"
          :class="device.is_online ? 'iot-status-dot--online' : 'iot-status-dot--offline'"
        ></span>
        <span class="type-name">{{ typeName }}</span>
      </div>
      <div class="device-card__actions">
        <span
          class="iot-status-tag"
          :class="device.is_online ? 'iot-status-tag--online' : 'iot-status-tag--offline'"
        >
          {{ device.is_online ? '在线' : '离线' }}
        </span>
        <el-icon v-if="showDelete" class="device-card__delete" @click.stop="$emit('delete', device)">
          <Close />
        </el-icon>
      </div>
    </div>

    <!-- 设备名称 -->
    <div class="device-card__name">{{ device.name }}</div>

    <!-- 状态区域：按 config_parameters 渲染最新值 -->
    <div class="device-card__data">
      <div v-for="field in fields" :key="field" class="data-item">
        <span class="data-item__label">{{ field }}</span>
        <span class="data-item__value">
          {{ formatState(latestValue(field)) }}
        </span>
      </div>
      <div v-if="!fields.length" class="iot-text-secondary" style="font-size: 12px;">
        未定义字段
      </div>
    </div>

    <!-- 底部：位置 + 时间 -->
    <div class="device-card__footer">
      <span class="footer-location" :title="device.location || '未设置'">
        {{ device.location || '未设置位置' }}
      </span>
      <span class="footer-time">
        {{ device.last_seen ? timeAgo(device.last_seen) : '从未上报' }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Close } from '@element-plus/icons-vue'

const props = defineProps({
  device: { type: Object, required: true },
  showDelete: { type: Boolean, default: true },
})

defineEmits(['click', 'delete'])

const typeName = computed(() => {
  return props.device.device_type_info?.name || '未知类型'
})

const fields = computed(() => {
  return props.device.device_type_info?.config_parameters || []
})

const latestData = computed(() => {
  return props.device.latest_data?.data || {}
})

function latestValue(field) {
  const val = latestData.value[field]
  if (val === undefined || val === null) return null
  return val
}

function formatState(val) {
  if (val === null || val === undefined) return '--'
  if (typeof val === 'boolean') return val ? '开' : '关'
  if (typeof val === 'number') return Number(val.toFixed(2))
  return String(val)
}

function timeAgo(dateStr) {
  const now = new Date()
  const past = new Date(dateStr)
  const diff = Math.floor((now - past) / 1000)
  if (diff < 5) return '刚刚'
  if (diff < 60) return `${diff}秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  return `${Math.floor(diff / 86400)}天前`
}
</script>

<style scoped>
.device-card {
  padding: var(--iot-spacing-lg);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.device-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.device-card__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.device-card__delete {
  font-size: 14px;
  color: var(--iot-text-secondary);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.2s, color 0.2s;
}

.device-card:hover .device-card__delete,
.device-card--touch .device-card__delete {
  opacity: 1;
}

/* 移动端：始终显示删除按钮 */
@media (max-width: 767px) {
  .device-card__delete {
    opacity: 0.7;
  }

  .device-card:hover .device-card__delete {
    opacity: 1;
  }
}

.device-card__delete:hover {
  color: var(--el-color-danger);
}

.device-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.type-name {
  font-size: var(--iot-font-size-xs);
  color: var(--iot-text-secondary);
  font-weight: 500;
}

.device-card__name {
  font-size: var(--iot-font-size-md);
  font-weight: 600;
  color: var(--iot-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-card__data {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  min-height: 40px;
}

.data-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 80px;
}

.data-item__label {
  font-size: 11px;
  color: var(--iot-text-secondary);
  text-transform: capitalize;
}

.data-item__value {
  font-size: 20px;
  font-weight: 700;
  color: var(--iot-text-primary);
  font-variant-numeric: tabular-nums;
}

.device-card__footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 10px;
  border-top: 1px solid var(--iot-border-color-lighter);
  font-size: var(--iot-font-size-xs);
  color: var(--iot-text-secondary);
}

.footer-location {
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
