<template>
  <div class="app-layout">
    <!-- 侧边栏 - 桌面端 -->
    <AppSidebar
      v-if="!isMobile"
      :collapsed="appStore.sidebarCollapsed"
      @toggle="appStore.toggleSidebar()"
    />

    <!-- 侧边栏 - 移动端抽屉 -->
    <el-drawer
      v-model="appStore.sidebarDrawerVisible"
      direction="ltr"
      size="min(86vw, 280px)"
      :show-close="false"
      :with-header="false"
      :append-to-body="true"
      class="mobile-sidebar-drawer"
    >
      <AppSidebar
        :collapsed="false"
        @toggle="appStore.sidebarDrawerVisible = false"
      />
    </el-drawer>

    <!-- 右侧内容区 -->
    <div
      class="app-layout__content"
      :style="contentStyle"
    >
      <AppHeader @toggle-drawer="appStore.sidebarDrawerVisible = true" />
      <AppMain />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import AppSidebar from './AppSidebar.vue'
import AppHeader from './AppHeader.vue'
import AppMain from './AppMain.vue'

const appStore = useAppStore()
const userStore = useUserStore()
const windowWidth = ref(window.innerWidth)

const isMobile = computed(() => windowWidth.value < 768)

const contentStyle = computed(() => {
  if (isMobile.value) {
    return { marginLeft: '0px' }
  }
  const width = appStore.sidebarCollapsed
    ? 'var(--iot-sidebar-collapsed-width)'
    : 'var(--iot-sidebar-width)'
  return { marginLeft: width }
})

function onResize() {
  windowWidth.value = window.innerWidth
  // 中等屏幕自动折叠
  if (windowWidth.value < 1200 && windowWidth.value >= 768) {
    appStore.sidebarCollapsed = true
  }
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  onResize()
  // 页面刷新后重新拉取用户信息，保证 isStaff 等权限判断正确
  if (userStore.isLoggedIn && !userStore.userInfo) {
    userStore.fetchUserInfo().catch(() => {})
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.app-layout {
  position: relative;
  min-height: 100vh;
}

.app-layout__content {
  min-height: 100vh;
  min-width: 0;
  transition: margin-left var(--iot-transition-base);
}

/* 移动端抽屉内侧边栏样式修正 */
:global(.mobile-sidebar-drawer .el-drawer__body) {
  padding: 0;
}

:global(.mobile-sidebar-drawer) {
  overflow: hidden;
  border: 0;
  border-radius: 0 var(--iot-radius-xl) var(--iot-radius-xl) 0;
}

:global(.mobile-sidebar-drawer .app-sidebar) {
  position: relative;
  width: 100% !important;
}

@media (prefers-reduced-motion: reduce) {
  .app-layout__content { transition: none; }
}
</style>
