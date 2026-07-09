<template>
  <div class="sensor-card iot-card iot-card--hover" @click="$emit('click', sensor)">
    <!-- 顶部：状态 + 类型名 -->
    <div class="sensor-card__header">
      <div class="sensor-card__title">
        <span
          class="iot-status-dot"
          :class="sensor.is_online ? 'iot-status-dot--online' : 'iot-status-dot--offline'"
        ></span>
        <span class="type-name">{{ typeName }}</span>
      </div>
      <div class="sensor-card__actions">
        <span
          class="iot-status-tag"
          :class="sensor.is_online ? 'iot-status-tag--online' : 'iot-status-tag--offline'"
        >
          {{ sensor.is_online ? '在线' : '离线' }}
        </span>
        <el-icon v-if="showDelete" class="sensor-card__delete" @click.stop="$emit('delete', sensor)">
          <Close />
        </el-icon>
      </div>
    </div>

    <!-- 传感器名称 -->
    <div class="sensor-card__name">{{ sensor.name }}</div>

    <!-- 数据区域：根据 data_fields 动态渲染 -->
    <div class="sensor-card__data">
      <div v-for="field in dataFields" :key="field" class="data-item">
        <span class="data-item__label">{{ field }}</span>
        <span class="data-item__value">
          {{ latestValue(field) ?? '--' }}
        </span>
      </div>
      <div v-if="!dataFields.length" class="iot-text-secondary" style="font-size: 12px;">
        未定义数据字段
      </div>
    </div>

    <!-- 底部：位置 + 时间 -->
    <div class="sensor-card__footer">
      <span class="footer-location" :title="sensor.location || '未设置'">
        {{ sensor.location || '未设置位置' }}
      </span>
      <span class="footer-time">
        {{ sensor.last_seen ? timeAgo(sensor.last_seen) : '从未上报' }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Close } from '@element-plus/icons-vue'

const props = defineProps({
  sensor: { type: Object, required: true },
  showDelete: { type: Boolean, default: true },
})

defineEmits(['click', 'delete'])

const typeName = computed(() => {
  return props.sensor.sensor_type_info?.name || '未知类型'
})

const dataFields = computed(() => {
  return props.sensor.sensor_type_info?.data_fields || []
})

const latestData = computed(() => {
  return props.sensor.latest_data?.data || {}
})

function latestValue(field) {
  const val = latestData.value[field]
  if (val === undefined || val === null) return null
  if (typeof val === 'number') return Number(val.toFixed(2))
  return val
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
.sensor-card {
  padding: var(--iot-spacing-lg);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sensor-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.sensor-card__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sensor-card__delete {
  font-size: 14px;
  color: var(--iot-text-secondary);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.2s, color 0.2s;
}

.sensor-card:hover .sensor-card__delete {
  opacity: 1;
}

/* 移动端：始终显示删除按钮 */
@media (max-width: 767px) {
  .sensor-card__delete {
    opacity: 0.7;
  }

  .sensor-card:hover .sensor-card__delete {
    opacity: 1;
  }
}

.sensor-card__delete:hover {
  color: var(--el-color-danger);
}

.sensor-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.type-name {
  font-size: var(--iot-font-size-xs);
  color: var(--iot-text-secondary);
  font-weight: 500;
}

.sensor-card__name {
  font-size: var(--iot-font-size-md);
  font-weight: 600;
  color: var(--iot-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sensor-card__data {
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

.sensor-card__footer {
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
