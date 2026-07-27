<template>
  <article
    class="resource-card iot-card iot-card--hover"
    :class="[
      variantClass,
      { 'resource-card--deletable': showDelete },
    ]"
  >
    <button
      type="button"
      class="resource-card__main"
      :aria-label="`查看${kindLabel}「${name}」`"
      @click="$emit('activate')"
    >
      <span class="resource-card__header" :class="`${variantClass}__header`">
        <span class="resource-card__title" :class="`${variantClass}__title`">
          <span
            class="iot-status-dot"
            :class="isOnline ? 'iot-status-dot--online' : 'iot-status-dot--offline'"
            aria-hidden="true"
          />
          <span class="resource-card__type-name">{{ typeName }}</span>
        </span>

        <span
          class="iot-status-tag"
          :class="isOnline ? 'iot-status-tag--online' : 'iot-status-tag--offline'"
        >
          {{ isOnline ? '在线' : '离线' }}
        </span>
      </span>

      <span class="resource-card__name" :class="`${variantClass}__name`">
        {{ name }}
      </span>

      <span class="resource-card__data" :class="`${variantClass}__data`">
        <span v-for="field in fields" :key="field.key" class="resource-card__data-item data-item">
          <span class="resource-card__data-label data-item__label">{{ field.label }}</span>
          <span class="resource-card__data-value data-item__value">{{ field.value }}</span>
        </span>
        <span v-if="!fields.length" class="resource-card__empty iot-text-secondary">
          {{ emptyText }}
        </span>
      </span>

      <span class="resource-card__footer" :class="`${variantClass}__footer`">
        <span class="resource-card__location footer-location" :title="location || '未设置'">
          {{ location || '未设置位置' }}
        </span>
        <span class="resource-card__time footer-time">
          {{ lastSeen ? timeAgo(lastSeen) : '从未上报' }}
        </span>
      </span>
    </button>

    <button
      v-if="showDelete"
      type="button"
      class="resource-card__delete"
      :class="`${variantClass}__delete`"
      :aria-label="`删除${kindLabel}「${name}」`"
      :title="`删除${kindLabel}「${name}」`"
      @click.stop="$emit('delete')"
    >
      <el-icon aria-hidden="true"><Close /></el-icon>
    </button>
  </article>
</template>

<script setup>
import { computed } from 'vue'
import { Close } from '@element-plus/icons-vue'

const props = defineProps({
  variant: {
    type: String,
    required: true,
    validator: (value) => ['sensor', 'device'].includes(value),
  },
  kindLabel: { type: String, required: true },
  name: { type: String, default: '' },
  typeName: { type: String, default: '' },
  isOnline: { type: Boolean, default: false },
  fields: { type: Array, default: () => [] },
  emptyText: { type: String, default: '' },
  location: { type: String, default: '' },
  lastSeen: { type: String, default: '' },
  showDelete: { type: Boolean, default: true },
})

defineEmits(['activate', 'delete'])

const variantClass = computed(() => `${props.variant}-card`)

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
.resource-card {
  overflow: hidden;
  cursor: pointer;
}

.resource-card:focus-within {
  border-color: color-mix(in srgb, var(--iot-color-primary) 42%, var(--iot-border-color-light));
  box-shadow:
    inset 0 1px 0 var(--iot-highlight-edge),
    var(--iot-shadow-base),
    var(--iot-focus-ring);
}

.resource-card:active {
  transform: scale(0.988);
  transition-duration: 80ms;
}

.resource-card__main {
  display: flex;
  width: 100%;
  min-height: 100%;
  flex-direction: column;
  gap: 12px;
  padding: var(--iot-spacing-lg);
  border: 0;
  border-radius: inherit;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.resource-card__main:focus-visible {
  box-shadow: none;
}

.resource-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--iot-spacing-sm);
}

.resource-card--deletable .resource-card__header {
  padding-right: 44px;
}

.resource-card__title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.resource-card__type-name {
  overflow: hidden;
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-xs);
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-card__name {
  overflow: hidden;
  color: var(--iot-text-primary);
  font-family: var(--iot-font-display);
  font-size: var(--iot-font-size-md);
  font-weight: 650;
  letter-spacing: -0.012em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-card__data {
  display: flex;
  min-height: 40px;
  flex-wrap: wrap;
  gap: 12px;
}

.resource-card__data-item {
  display: flex;
  min-width: 80px;
  flex-direction: column;
  gap: 2px;
}

.resource-card__data-label {
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-xs);
  text-transform: capitalize;
}

.resource-card__data-value {
  color: var(--iot-text-primary);
  font-family: var(--iot-font-display);
  font-size: var(--iot-font-size-lg);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.018em;
}

.resource-card__empty {
  align-self: center;
  font-size: var(--iot-font-size-xs);
}

.resource-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--iot-spacing-sm);
  padding-top: 10px;
  border-top: 1px solid var(--iot-separator);
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-xs);
}

.resource-card__location {
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-card__time {
  flex: none;
}

.resource-card__delete {
  position: absolute;
  z-index: 3;
  top: 10px;
  right: 10px;
  display: grid;
  width: 44px;
  height: 44px;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: var(--iot-radius-pill);
  color: var(--iot-text-secondary);
  background: transparent;
  cursor: pointer;
  opacity: 0.68;
  transition:
    color var(--iot-transition-fast),
    background-color var(--iot-transition-fast),
    opacity var(--iot-transition-fast),
    transform var(--iot-transition-instant);
}

.resource-card__delete:hover,
.resource-card__delete:focus-visible {
  color: var(--iot-color-danger);
  background: var(--iot-color-danger-bg);
  opacity: 1;
}

.resource-card__delete:active {
  transform: scale(0.9);
}

@media (hover: hover) and (pointer: fine) {
  .resource-card__delete {
    opacity: 0;
  }

  .resource-card:hover .resource-card__delete,
  .resource-card:focus-within .resource-card__delete {
    opacity: 0.78;
  }

  .resource-card__delete:hover,
  .resource-card__delete:focus-visible {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .resource-card:active,
  .resource-card__delete:active {
    transform: none;
  }
}
</style>
