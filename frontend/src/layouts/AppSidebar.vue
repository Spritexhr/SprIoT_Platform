<template>
  <div class="app-sidebar" :class="{ 'app-sidebar--collapsed': collapsed }">
    <!-- Logo 区域 -->
    <div class="app-sidebar__logo">
      <div class="logo-icon">
        <svg viewBox="0 0 32 32" width="28" height="28" fill="none" aria-hidden="true">
          <rect x="2" y="2" width="28" height="28" rx="9" fill="currentColor" opacity="0.12"/>
          <circle cx="16" cy="16" r="4" fill="currentColor"/>
          <circle cx="9" cy="10" r="2" fill="currentColor" opacity="0.72"/>
          <circle cx="24" cy="9" r="2" fill="currentColor" opacity="0.72"/>
          <circle cx="23" cy="24" r="2" fill="currentColor" opacity="0.72"/>
          <path d="M10.6 11.4 13.2 14M20 13.2l2.5-2.7M19.2 19.1l2.4 3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
        </svg>
      </div>
      <transition name="fade">
        <span v-show="!collapsed" class="logo-copy">
          <strong class="logo-text">SprIoT</strong>
          <small>Control Center</small>
        </span>
      </transition>
    </div>

    <!-- 导航菜单 -->
    <transition name="fade">
      <div v-show="!collapsed" class="nav-section-label">{{ ls.locale === 'zh' ? '工作区' : 'Workspace' }}</div>
    </transition>
    <el-menu
      :default-active="activeMenu"
      :collapse="collapsed"
      :collapse-transition="false"
      router
      class="app-sidebar__menu"
    >
      <el-menu-item index="/">
        <el-icon><Odometer /></el-icon>
        <template #title>{{ ls.t('nav.dashboard') }}</template>
      </el-menu-item>

      <el-menu-item index="/sensors">
        <el-icon><Cpu /></el-icon>
        <template #title>{{ ls.t('nav.sensors') }}</template>
      </el-menu-item>

      <el-menu-item index="/devices">
        <el-icon><Monitor /></el-icon>
        <template #title>{{ ls.t('nav.devices') }}</template>
      </el-menu-item>

      <el-menu-item index="/automation">
        <el-icon><SetUp /></el-icon>
        <template #title>{{ ls.t('nav.automation') }}</template>
      </el-menu-item>

      <el-menu-item index="/projects">
        <el-icon><Grid /></el-icon>
        <template #title>{{ ls.t('nav.projects') }}</template>
      </el-menu-item>

      <el-menu-item index="/plugins">
        <el-icon><Connection /></el-icon>
        <template #title>{{ ls.t('nav.plugins') }}</template>
      </el-menu-item>

      <el-menu-item index="/settings">
        <el-icon><Setting /></el-icon>
        <template #title>{{ ls.t('nav.settings') }}</template>
      </el-menu-item>
    </el-menu>

    <!-- 底部折叠按钮 -->
    <div class="app-sidebar__footer">
      <button
        type="button"
        class="collapse-btn"
        :aria-label="collapsed ? (ls.locale === 'zh' ? '展开菜单' : 'Expand menu') : ls.t('nav.collapse')"
        @click="$emit('toggle')"
      >
        <el-icon :size="18">
          <Fold v-if="!collapsed" />
          <Expand v-else />
        </el-icon>
        <transition name="fade">
          <span v-show="!collapsed" class="collapse-text">{{ ls.t('nav.collapse') }}</span>
        </transition>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useLocaleStore } from '@/stores/locale'
import {
  Odometer,
  Cpu,
  Monitor,
  SetUp,
  Setting,
  Connection,
  Grid,
  Fold,
  Expand,
} from '@element-plus/icons-vue'

const ls = useLocaleStore()

defineProps({
  collapsed: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['toggle'])

const route = useRoute()

const activeMenu = computed(() => {
  if (route.path === '/') return '/'
  return '/' + route.path.split('/')[1]
})
</script>

<style scoped>
.app-sidebar {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 1001;
  display: flex;
  flex-direction: column;
  width: var(--iot-sidebar-width);
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  border-right: 1px solid var(--iot-border-color-light);
  background: var(--iot-bg-sidebar);
  box-shadow: inset -1px 0 0 var(--iot-highlight-edge), 12px 0 36px rgba(39, 33, 29, 0.035);
  backdrop-filter: blur(var(--iot-material-blur-lg)) saturate(170%);
  -webkit-backdrop-filter: blur(var(--iot-material-blur-lg)) saturate(170%);
  transition: width var(--iot-transition-base), background-color var(--iot-transition-base);
}

.app-sidebar--collapsed {
  width: var(--iot-sidebar-collapsed-width);
}

.app-sidebar__logo {
  display: flex;
  align-items: center;
  gap: 11px;
  height: var(--iot-header-height);
  flex-shrink: 0;
  overflow: hidden;
  padding: 0 17px;
}

.logo-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  border: 1px solid color-mix(in srgb, var(--iot-color-primary) 18%, var(--iot-border-color-light));
  border-radius: 13px;
  background: linear-gradient(145deg, var(--iot-bg-card-hover), var(--iot-color-primary-bg));
  color: var(--iot-color-primary);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-xs);
}

.logo-copy {
  display: grid;
  min-width: 0;
  line-height: 1;
}

.logo-text {
  color: var(--iot-text-primary);
  font-family: var(--iot-font-display);
  font-size: 16px;
  font-weight: 680;
  letter-spacing: -0.025em;
  white-space: nowrap;
}

.logo-copy small {
  margin-top: 5px;
  color: var(--iot-text-secondary);
  font-size: 9px;
  font-weight: 650;
  letter-spacing: 0.095em;
  text-transform: uppercase;
  white-space: nowrap;
}

.nav-section-label {
  height: 29px;
  flex: none;
  padding: 9px 21px 4px;
  color: var(--iot-text-tertiary);
  font-size: 10px;
  font-weight: 680;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  white-space: nowrap;
}

.app-sidebar__menu {
  flex: 1;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 6px 10px 12px;
  border-right: none !important;
  background: transparent !important;
}

.app-sidebar__menu :deep(.el-menu) {
  border-right: none !important;
  background: transparent !important;
}

.app-sidebar__menu :deep(.el-menu-item) {
  position: relative;
  height: 44px;
  margin: 3px 0;
  border: 1px solid transparent;
  border-radius: 13px;
  color: var(--iot-sidebar-text);
  font-size: var(--iot-font-size-sm);
  line-height: 44px;
  transition: color var(--iot-transition-fast), background-color var(--iot-transition-fast), border-color var(--iot-transition-fast), transform var(--iot-transition-instant), box-shadow var(--iot-transition-fast);
}

.app-sidebar__menu :deep(.el-menu-item:hover) {
  background: var(--iot-sidebar-item-hover);
  color: var(--iot-sidebar-text-active);
}

.app-sidebar__menu :deep(.el-menu-item:active) {
  transform: scale(0.975);
}

.app-sidebar__menu :deep(.el-menu-item.is-active) {
  border-color: var(--iot-border-color-light);
  background: var(--iot-sidebar-item-active);
  color: var(--iot-sidebar-text-active);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-xs);
  font-weight: 610;
}

.app-sidebar__menu :deep(.el-menu-item.is-active::before) {
  position: absolute;
  top: 50%;
  left: 5px;
  width: 3px;
  height: 17px;
  border-radius: var(--iot-radius-pill);
  background: var(--iot-color-primary);
  content: '';
  transform: translateY(-50%);
}

.app-sidebar__menu :deep(.el-menu-item.is-active .el-icon) {
  color: var(--iot-color-primary);
}

.app-sidebar__menu :deep(.el-menu-item .el-icon) {
  margin-right: 10px;
  font-size: 18px;
}

.app-sidebar__menu :deep(.el-menu--collapse .el-menu-item) {
  padding: 0 !important;
  justify-content: center;
}

.app-sidebar__menu :deep(.el-menu--collapse .el-menu-item .el-icon) {
  margin-right: 0;
}

.app-sidebar__footer {
  flex-shrink: 0;
  padding: 10px;
  border-top: 1px solid var(--iot-separator);
}

.collapse-btn {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 42px;
  gap: 8px;
  overflow: hidden;
  padding: 8px 13px;
  border: 0;
  border-radius: 13px;
  background: transparent;
  color: var(--iot-sidebar-text);
  cursor: pointer;
  text-align: left;
  transition: color var(--iot-transition-fast), background-color var(--iot-transition-fast), transform var(--iot-transition-instant);
}

.collapse-btn:hover {
  background: var(--iot-sidebar-item-hover);
  color: var(--iot-sidebar-text-active);
}

.collapse-btn:active { transform: scale(0.975); }

.collapse-text {
  font-size: var(--iot-font-size-sm);
  white-space: nowrap;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 180ms ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.app-sidebar--collapsed .app-sidebar__logo { padding: 0 18px; }
.app-sidebar--collapsed .app-sidebar__menu { padding-inline: 10px; }
.app-sidebar--collapsed .collapse-btn { justify-content: center; padding-inline: 0; }

@media (prefers-reduced-transparency: reduce) {
  .app-sidebar {
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-sidebar__menu :deep(.el-menu-item:active),
  .collapse-btn:active { transform: none; }
}
</style>
