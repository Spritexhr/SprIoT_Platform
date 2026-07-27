<template>
  <ResourceCard
    variant="device"
    kind-label="设备"
    :name="device.name"
    :type-name="typeName"
    :is-online="device.is_online"
    :fields="displayFields"
    empty-text="未定义字段"
    :location="device.location"
    :last-seen="device.last_seen"
    :show-delete="showDelete"
    @activate="$emit('click', device)"
    @delete="$emit('delete', device)"
  />
</template>

<script setup>
import { computed } from 'vue'
import ResourceCard from '@/components/resources/ResourceCard.vue'

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

const displayFields = computed(() => {
  return fields.value.map((field) => ({
    key: field,
    label: field,
    value: formatState(latestValue(field)),
  }))
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
</script>
