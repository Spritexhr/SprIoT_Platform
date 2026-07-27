<template>
  <ResourceCard
    variant="sensor"
    kind-label="传感器"
    :name="sensor.name"
    :type-name="typeName"
    :is-online="sensor.is_online"
    :fields="displayFields"
    empty-text="未定义数据字段"
    :location="sensor.location"
    :last-seen="sensor.last_seen"
    :show-delete="showDelete"
    @activate="$emit('click', sensor)"
    @delete="$emit('delete', sensor)"
  />
</template>

<script setup>
import { computed } from 'vue'
import ResourceCard from '@/components/resources/ResourceCard.vue'

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

const displayFields = computed(() => {
  return dataFields.value.map((field) => ({
    key: field,
    label: field,
    value: latestValue(field) ?? '--',
  }))
})

function latestValue(field) {
  const val = latestData.value[field]
  if (val === undefined || val === null) return null
  if (typeof val === 'number') return Number(val.toFixed(2))
  return val
}
</script>
