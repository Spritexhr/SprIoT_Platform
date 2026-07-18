<template>
  <main class="app-main">
    <router-view v-slot="{ Component }">
      <transition name="fade-transform" mode="out-in">
        <component :is="Component" />
      </transition>
    </router-view>
  </main>
</template>

<script setup>
</script>

<style scoped>
.app-main {
  width: 100%;
  max-width: var(--iot-content-max-width);
  min-height: calc(100vh - var(--iot-header-height));
  margin: 0 auto;
  padding: clamp(22px, 2.25vw, 38px);
  padding-bottom: max(clamp(28px, 3vw, 48px), env(safe-area-inset-bottom));
  transition: padding var(--iot-transition-base);
}

@media (max-width: 767px) {
  .app-main {
    padding: 20px 14px max(28px, env(safe-area-inset-bottom));
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-main { transition: none; }
}
</style>
