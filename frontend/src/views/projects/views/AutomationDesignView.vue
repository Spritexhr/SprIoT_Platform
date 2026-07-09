<template>
  <div class="adv">
    <el-tabs v-model="activeMode" class="adv__tabs">
      <el-tab-pane name="scheme">
        <template #label>
          <span class="adv__tab-label"><el-icon><Operation /></el-icon>控制方案</span>
        </template>
        <ControlSchemeView :project-id="projectId" :section-id="sectionId" />
      </el-tab-pane>
      <el-tab-pane name="script">
        <template #label>
          <span class="adv__tab-label"><el-icon><Document /></el-icon>脚本规则</span>
        </template>
        <ScriptRuleView :project-id="projectId" :section-id="sectionId" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Document, Operation } from '@element-plus/icons-vue'
import ControlSchemeView from './ControlSchemeView.vue'
import ScriptRuleView from './ScriptRuleView.vue'

defineProps({
  projectId: { type: Number, required: true },
  sectionId: { type: [Number, null], default: null },
})

const activeMode = ref('scheme')
</script>

<style scoped lang="scss">
.adv { width: 100%; }
.adv__tabs :deep(.el-tabs__content) {
  width: 100%;
  overflow: visible;
}
.adv__tabs :deep(.el-tab-pane) {
  width: 100%;
}
:global(.pw__tabs.segmented-tabs .adv__tabs > .el-tabs__header) {
  display: inline-flex;
  align-self: flex-start;
  width: auto;
  padding: 0;
  border: 0;
  border-radius: 0;
  margin-bottom: var(--iot-spacing-md);
  background: transparent;
}
.adv__tabs :deep(.el-tabs__nav-wrap::after),
.adv__tabs :deep(.el-tabs__active-bar) { display: none; }
.adv__tabs :deep(.el-tabs__nav) {
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-base);
  background: var(--iot-border-color-lighter);
}
.adv__tabs :deep(.el-tabs__item) {
  height: 32px;
  padding: 0 14px;
  border-radius: 4px;
  color: var(--iot-text-secondary);
}
.adv__tabs :deep(.el-tabs__item.is-active) {
  color: var(--iot-text-primary);
  background: var(--iot-bg-card);
  box-shadow: var(--iot-shadow-sm);
}
.adv__tab-label { display: inline-flex; align-items: center; gap: 6px; }
</style>
